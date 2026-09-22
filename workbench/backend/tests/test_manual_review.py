import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services import manual_review as m
from app.services.history import atomic_json_write
from app.config import PROJECT_ROOT


def row(**overrides):
    raw = {"match_id": "1234", "competition": "测试杯", "match_number": "周二001", "home_team": "甲", "away_team": "乙", "kickoff_time": "2026-09-22T18:00:00+08:00", "official_handicap": -1, "result": "胜", "handicap_result": "让平", "raw_probabilities": {"result": {"胜": .6, "平": .25, "负": .15}}, **overrides}
    return m.normalize(raw, {"created_at": "2026-09-22T08:00:00+00:00"})


def result(**overrides):
    return {"matchId": 1234, "homeTeam": "甲", "awayTeam": "乙", "leagueNameAbbr": "测试杯", "matchNumStr": "周二001", "matchDate": "2026-09-22", "matchResultStatus": "2", "poolStatus": "Payout", "sectionsNo999": "2:0", "_source": {"url": m.RESULT_API, "fetched_at": "2026-09-22T22:00:00+08:00"}, **overrides}


def test_real_two_play_denominators_and_brier_sum():
    r = row()
    compared = m.compare_match(r, m.match_result(r, [result()]), "research")
    assert compared["comparison"]["result"]["hit"] is True
    assert compared["comparison"]["handicap_result"]["hit"] is False
    assert compared["comparison"]["result"]["brier"] == pytest.approx(.245)
    assert compared["lesson_candidates"] == []  # Never create a rule for each miss.
    stats = m.summarize([compared])
    assert stats["combined"]["denominator"] == 2 and stats["combined"]["hits"] == 1


@pytest.mark.parametrize("change", [{"sectionsNo999": ""}, {"sectionsNo999": "取消"}, {"matchResultStatus": "1"}, {"poolStatus": "Selling"}, {"matchResultStatus": "3"}])
def test_missing_cancel_live_never_zero_or_failure(change):
    r = row()
    actual = m.match_result(r, [result(**change)])
    assert actual["score"] is None and not actual["verified_90_minutes"]
    report = m.compare_match(r, actual, "research")
    assert report["comparison"]["result"]["hit"] is None


def test_postgame_or_naive_times_never_scored():
    for saved in ("2026-09-22T11:00:00+00:00", "2026-09-22T08:00:00", None):
        r = row(); r["pregame_eligible"] = False
        report = m.compare_match(r, m.match_result(r, [result()]), "research")
        assert m.summarize([report])["combined"]["denominator"] == 0
        assert report["comparison"]["result"]["brier"] is None
    raw = {"match_id": "1234", "kickoff_time": "2026-09-22T18:00:00+08:00"}
    assert not m.normalize(raw, {"created_at": "2026-09-22T11:00:00+00:00"})["pregame_eligible"]


def test_identity_duplicates_and_aliases_fail_closed():
    r = row()
    assert not m.match_result(r, [result(homeTeam="别名")])["verified_90_minutes"]
    assert not m.match_result(r, [result(), result()])["verified_90_minutes"]
    r["official_match_id"] = ""
    assert m.match_result(r, [result()])["identity_basis"] == "exact_official_names_number_date"
    assert not m.match_result(r, [result(matchDate="2026-09-23")])["verified_90_minutes"]


def test_pending_status_distinguishes_unplayed_and_not_published():
    r = row(kickoff_time="2099-09-22T18:00:00+08:00")
    assert "尚未开赛" in m.match_result(r, [])["reason"]
    r = row(kickoff_time="2000-09-22T18:00:00+08:00")
    assert "尚未发布" in m.match_result(r, [])["reason"]
    assert "冲突" not in m.match_result(r, [])["reason"]


def test_independent_probability_precedence_and_invalid_vectors():
    r = row(independent_model={"analysis_method": "model-a", "raw_probabilities": {"result": {"胜": .2, "平": .3, "负": .5}}})
    assert r["model_method"] == "model-a" and m._probabilities(r, "result")["负"] == .5
    for values in ({"胜": .6, "平": None, "负": .4}, {"胜": .6, "平": .6, "负": .4}, {"胜": float("nan"), "平": .3, "负": .4}):
        r["probabilities"] = {"result": values}
        assert m._probabilities(r, "result") is None


def test_analysis_direction_research_only():
    r = row(result=None, analysis_result="胜")
    actual = m.match_result(r, [result()])
    report = m.compare_match(r, actual, "research")
    assert report["comparison"]["result"]["analysis_only"] is True
    assert report["comparison"]["result"]["included"] is True
    r["formal_eligible"] = True
    assert not m.compare_match(r, actual, "historical_version")["comparison"]["result"]["included"]


def test_report_hash_and_lesson_scope_and_idempotence(tmp_path):
    settings = SimpleNamespace(runtime_dir=tmp_path)
    r = row(); r["result"] = m.match_result(r, [result()])
    r["lesson_candidates"] = [{"candidate_id": "a" * 20, "text": "样例有依据的检查", "scope": {"competition": "测试杯", "official_handicap": -1, "model_method": "model-a"}}]
    report = {"report_id": "b" * 32, "matches": [r]}
    report["report_sha256"] = m.digest(report)
    path = tmp_path / "reviews" / "manual" / ("b" * 32 + ".json")
    atomic_json_write(path, report)
    first = m.adopt_lesson(settings, report["report_id"], r["match_id"], "a" * 20)
    second = m.adopt_lesson(settings, report["report_id"], r["match_id"], "a" * 20)
    assert first["status"] == "adopted" and second["status"] == "already_adopted"
    fixture = {"competition": "测试杯", "official_handicap": -1, "model_method": "model-a", "kickoff_time": "2099-01-01T10:00:00+08:00"}
    assert len(m.load_applicable_lessons(tmp_path, fixture)) == 1
    assert not m.load_applicable_lessons(tmp_path, {**fixture, "competition": "其他杯"})
    assert not m.load_applicable_lessons(tmp_path, {k: v for k, v in fixture.items() if k != "model_method"})
    report["matches"] = []
    atomic_json_write(path, report)
    with pytest.raises(ValueError, match="校验码"):
        m.read_report(settings, report["report_id"])


def test_run_immutable_and_fetch_errors_have_complete_local_report(tmp_path, monkeypatch):
    settings = SimpleNamespace(runtime_dir=tmp_path, llm_enabled=False)
    batch = {"batch_id": "draft:" + "a" * 32, "kind": "research", "source_sha256": "b" * 64, "matches": [row()]}
    before = deepcopy(batch)
    monkeypatch.setattr(m, "load_batch", lambda *_: batch)
    monkeypatch.setattr(m, "fetch_result_day", lambda *_: (_ for _ in ()).throw(OSError("network")))
    r = m.run_review(settings, batch["batch_id"], use_astra=False)
    assert batch == before and r["status"] == "partial_pending"
    assert r["fetch_errors"] and r["statistics"]["combined"]["denominator"] == 0
    assert m.read_report(settings, r["report_id"]) == r


def test_astra_no_tool_or_probability_and_process_packet(tmp_path, monkeypatch):
    r = m.compare_match(row(), m.match_result(row(), [result()]), "research")
    r["process"] = {"facts": [{"fact_id": "p1", "summary": "67分钟主队进球", "source_url": m.RESULT_API}]}
    monkeypatch.setattr(m.evidence_llm, "status", lambda: {"available": True})
    monkeypatch.setattr(m.evidence_llm, "_binary", lambda: "codex.exe")
    out = {"matches": [{"match_id": r["match_id"], "reasoning_gap": "原判断未体现净胜区间", "hypothesis": "具体原因仍待核实", "next_check": "核实后续比赛首发和比分过程", "lesson": {"text": "检查领先后继续进球的实际证据，不推定只有一球优势", "trigger": "同赛事同让球再次以一球优势作为主路径时", "exclusions": "没有确认首发和可比过程材料时不调整", "fact_ids": ["p1"]}}]}
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e, ensure_ascii=False) for e in [{"type": "turn.started"}, {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(out, ensure_ascii=False)}}, {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}}]))
    monkeypatch.setattr(m.evidence_llm, "_run", run)
    report = m.astra_review([r], work_dir=tmp_path)
    assert report["status"] == "completed" and report["calls"] == 1
    args, kwargs = calls[0]
    assert "--sandbox" in args and "read-only" in args and "process_facts" in kwargs["input"]
    assert "raw_probabilities" not in kwargs["input"]
    out["matches"][0]["lesson"]["fact_ids"] = ["invented"]
    assert m.astra_review([r], work_dir=tmp_path)["status"] == "unavailable"


def test_formal_schema_fact_ledger_handicap_and_conflict_isolation(tmp_path):
    original = {"prediction_date": "2026-09-22", "prediction_version": 1, "state": "formal",
                "finalized_at": "2026-09-22T16:00:00+08:00", "kickoff_time": "2026-09-22T18:00:00+08:00",
                "match_number": "周二001", "competition": "测试杯", "home_team": "甲", "away_team": "乙",
                "official_data": {"handicap": {"official_handicap": -1}},
                "prediction": {"prediction_plays": ["result"], "predicted_result": "胜", "prediction_reason": "赛前理由", "favorite_no_win_path": "对方反击威胁", "counter_evidence_effect": "降低置信度"},
                "pre_match_facts": {"injuries": {"confirmed": False, "note": "待核实"}, "fact_sources": [{"url": "https://www.sporttery.cn/", "checked_at": "2026-09-22T16:00:00+08:00"}]}}
    normalized = m.normalize(original, {})
    assert normalized["official_handicap"] == -1 and normalized["formal_eligible"]
    assert normalized["counterevidence"] == ["强方不胜路径：对方反击威胁", "反证实际影响：降低置信度"]
    assert normalized["facts_before"][0]["dimension"] == "injuries"
    assert "待核实" in normalized["facts_before"][0]["summary"]
    conflict = deepcopy(original)
    conflict["prediction"]["predicted_result"] = "负"
    history = tmp_path / "history.json"
    atomic_json_write(history, {"records": [original, conflict]})
    settings = SimpleNamespace(history_path=history, runtime_dir=tmp_path, project_root=PROJECT_ROOT)
    batch = m.load_batch(settings, "history:2026-09-22:1")
    assert len(batch["matches"]) == 1
    r = batch["matches"][0]
    assert r["version_conflict"] and not r["formal_eligible"] and not r["probabilities"]
    comparison = m.compare_match(r, m.match_result(r, [result()]), "historical_version")
    assert comparison["comparison"]["result"]["exclusion_reason"] == "同编号同版本记录冲突，已隔离"
    assert "effective:2026-09-22" not in m._history_groups(settings)
    atomic_json_write(history, {"records": [original, original]})
    assert len(m.load_batch(settings, "history:2026-09-22:1")["matches"]) == 1
