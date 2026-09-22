from datetime import datetime, timedelta, timezone

from app.schemas import Fixture, OfficialPlay
from app.services import evidence_workflow as flow
from app.services.workflows import run_daily_prediction
from app.services.model import GoalModel


class EmptyPrefilter:
    def match(self, *args, **kwargs):
        return []


def fixture(index=1):
    return Fixture(sequence=index, match_id=str(index), match_number=str(index), business_date="2026-09-22", competition="未覆盖", home_team="主", away_team="客", kickoff_time=datetime.now(timezone.utc)+timedelta(days=2), official_handicap=-1, result_play=OfficialPlay(status="销售中",home=2,draw=3,away=4),handicap_play=OfficialPlay(status="销售中",home=2,draw=3,away=4))


def bundle(fixture, **kwargs):
    return {"identity_verified": True, "sources": [{"source_id": "s"}], "facts": [{"fact_id": "f1", "summary": "可核实事实", "dimension": "近况", "source_ids": ["s"]}], "teams": {"home": {"team_id": "10", "recent_matches": []}, "away": {"team_id": "20", "recent_matches": []}}, "missing": [], "warnings": []}


def suggestion(items, **kwargs):
    return {"status": "completed", "calls": 1, "usage": {"input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 20}, "reason": None, "matches": [{"match_id": i["fixture"]["match_id"], "analysis_result": "胜", "analysis_handicap_result": "让胜", "summary": "低置信证据判断", "support_fact_ids": ["f1"], "counter_fact_ids": [], "counterevidence_effect": "降低置信", "confidence": "低", "missing": [], "handicap_reason": "需要多球路径"} for i in items]}


def test_missing_models_auto_collect_once_batch_preserve_order_and_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "collect_fixture_evidence", bundle)
    calls = []
    def fake(items, **kwargs):
        calls.append(items)
        return suggestion(items, **kwargs)
    monkeypatch.setattr(flow, "suggest_evidence_batch", fake)
    fixtures = [fixture(1),fixture(2)]
    fixtures[1].result_play.status = "未开售"
    doc = run_daily_prediction(fixtures,model_paths={},prefilter=EmptyPrefilter(),rule_budget=5,output_dir=tmp_path/'drafts',evidence_dir=tmp_path/'evidence',llm_enabled=True)
    assert len(calls) == 1 and len(calls[0]) == 2
    assert [r["match_id"] for r in doc["matches"]] == ["1", "2"]
    assert doc["matches"][0]["result"] == "胜"
    assert doc["matches"][1]["result"] is None
    assert doc["matches"][1]["analysis_result"] == "胜"
    assert doc["llm_calls"] == 1 and doc["llm_usage"]["input_tokens"] == 100
    assert all(r["freeze_eligible"] is False and r["raw_probabilities"] is None for r in doc["matches"])
    assert "result_play" not in calls[0][0]["fixture"]


def test_provider_timeout_keeps_evidence_and_reports_truth(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "collect_fixture_evidence", bundle)
    monkeypatch.setattr(flow, "suggest_evidence_batch", lambda *a, **k: {"status":"unavailable","calls":1,"usage":None,"matches":[],"reason":"timeout"})
    row = {}
    doc = flow.fill_evidence_fallbacks([(fixture(),row)],evidence_dir=tmp_path)
    assert doc["llm_status"] == "unavailable" and doc["llm_usage"] is None
    assert row["evidence"]["facts"]
    assert row["analysis_status"] == "insufficient_evidence"
    assert row["result"] is None
    assert any("timeout" in s for s in row["missing"])


def test_llm_cannot_bypass_exact_margin_guard_with_just_a_citation(monkeypatch, tmp_path):
    monkeypatch.setattr(flow, "collect_fixture_evidence", bundle)
    def unproven(items, **kwargs):
        result = suggestion(items)
        result['matches'][0]['analysis_handicap_result'] = '让平'
        result['matches'][0]['handicap_reason'] = '参考f1'
        return result
    monkeypatch.setattr(flow, "suggest_evidence_batch", unproven)
    row = {}
    flow.fill_evidence_fallbacks([(fixture(),row)],evidence_dir=tmp_path)
    assert row['result'] == '胜'
    assert row['handicap_result'] is row['analysis_handicap_result'] is None
    assert '已撤下' in row['handicap_reason']


def test_one_broken_evidence_row_does_not_drop_slate(monkeypatch, tmp_path):
    def partial(item, **kwargs):
        if item.match_id == "1":
            raise ValueError("malformed")
        return bundle(item)
    monkeypatch.setattr(flow, "collect_fixture_evidence", partial)
    monkeypatch.setattr(flow, "suggest_evidence_batch", suggestion)
    rows = [{}, {}]
    flow.fill_evidence_fallbacks(list(zip([fixture(1),fixture(2)], rows)),evidence_dir=tmp_path)
    assert rows[0]["analysis_status"] == "blocked" and rows[0]["result"] is None
    assert rows[1]["result"] == "胜"


def test_started_while_analyzing_suppresses_output(monkeypatch, tmp_path):
    f = fixture()
    monkeypatch.setattr(flow, "collect_fixture_evidence", bundle)
    def late(items, **kwargs):
        f.kickoff_time = datetime.now(timezone.utc)-timedelta(seconds=1)
        return suggestion(items)
    monkeypatch.setattr(flow, "suggest_evidence_batch", late)
    row = {}
    flow.fill_evidence_fallbacks([(f,row)],evidence_dir=tmp_path)
    assert row["analysis_status"] == "blocked"
    assert row["result"] is row["handicap_result"] is row["analysis_result"] is None


def test_integrity_or_cutoff_error_does_not_bypass_via_evidence(monkeypatch, tmp_path, training_matches):
    model = GoalModel.fit(training_matches,competition="测试联赛",model_type="poisson")
    path = tmp_path/'model.json'
    model.save(path)
    item = fixture()
    item.competition = "测试联赛"
    item.kickoff_time = datetime.combine(model.training_cutoff,datetime.min.time(),timezone.utc)
    monkeypatch.setattr(flow,"collect_fixture_evidence",lambda *a,**k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    doc = run_daily_prediction([item],model_path=path,prefilter=EmptyPrefilter(),rule_budget=5,output_dir=tmp_path/'drafts',evidence_dir=tmp_path/'evidence',llm_enabled=True)
    assert not doc["evidence_fetch_performed"] and doc["llm_calls"] == 0
    assert "未来数据泄漏" in doc["matches"][0]["reason"]
