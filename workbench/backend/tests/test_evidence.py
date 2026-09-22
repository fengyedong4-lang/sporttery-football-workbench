from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.schemas import Fixture
from app.services import evidence


NOW = datetime(2026, 9, 22, 6, tzinfo=timezone.utc)


def fixture():
    return Fixture(sequence=1, match_id="123", match_number="001", business_date="2026-09-22", competition="杯赛", home_team="主队", away_team="客队", kickoff_time="2026-09-23T02:00:00+08:00")


def recent(**overrides):
    return {"matchId": 999, "matchDate": "2026-09-20", "homeTeamId": 10, "awayTeamId": 20,
            "homeTeamShortName": "主队", "awayTeamShortName": "客队", "homeTeamFullCourtGoalCnt": "1", "awayTeamFullCourtGoalCnt": "0",
            "tournamentShortName": "联赛", "tournamentId": 30, "seasonId": 40, **overrides}


def sources(kind, match_id, *, output_dir):
    head = {"sportteryMatchId": 123, "sportteryHomeTeamId": 10, "sportteryAwayTeamId": 20, "homeTeamShortName": "主队", "awayTeamShortName": "客队", "tournamentCnShortName": "杯赛", "matchDateTime": "2026-09-23 02:00"}
    value = head if kind == "head" else {
        side: {"statistics": {"teamId": team_id}, "sportteryTeamId": team_id, "matchList": [recent()] if kind == "recent" else [], "injuriesAndSuspensionsList": []}
        for side, team_id in (("home", 10), ("away", 20))
    }
    return value, {"source_id": kind, "url": "https://webapi.sporttery.cn/", "fetched_at": NOW.isoformat(), "source_updated_at": None}


def test_collect_exact_identity_date_only_and_no_odds(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "fetch_evidence_source", sources)
    result = evidence.collect_fixture_evidence(fixture(), output_dir=tmp_path, as_of=NOW)
    assert result["identity_verified"]
    assert len(result["sources"]) == 5
    assert result["teams"]["home"]["recent_matches"][0]["kickoff_time"] is None
    assert len(result["facts"]) == 5
    assert any("不等于全员健康" in text for text in result["missing"])
    assert all(fact["fact_id"] and fact["source_ids"] for fact in result["facts"])
    assert "odds" not in str(result)


def test_identity_mismatch_blocks_other_fetches(monkeypatch, tmp_path):
    calls = []
    def changed(kind, *args, **kwargs):
        calls.append(kind)
        value, source = sources(kind, *args, **kwargs)
        value["awayTeamShortName"] = "同名青年队"
        return value, source
    monkeypatch.setattr(evidence, "fetch_evidence_source", changed)
    result = evidence.collect_fixture_evidence(fixture(), output_dir=tmp_path, as_of=NOW)
    assert not result["identity_verified"]
    assert not result["facts"]
    assert calls == ["head"]


def test_filters_bad_id_youth_old_friendly_today_future_duplicate_and_scores():
    rows = [recent(), recent(), recent(matchId=1, homeTeamId=11), recent(matchId=2, matchDate="2023-01-01"),
            recent(matchId=3, tournamentShortName="俱乐部赛"), recent(matchId=4, matchDate="2026-09-22"),
            recent(matchId=5, matchDate="2026-09-23"), recent(matchId=6, homeTeamFullCourtGoalCnt=""), recent(matchId=7, homeTeamId=None)]
    kept, excluded = evidence._filter_rows(rows, {"10"}, "source", NOW)
    assert len(kept) == 1
    assert sum(excluded.values()) == 8


def test_exact_subject_with_missing_opponent_id_is_still_usable():
    kept, _ = evidence._filter_rows([recent(awayTeamId=0)], {"10"}, "source", NOW)
    assert len(kept) == 1
    assert kept[0]["away_team_id"] == ""


def test_conflicting_duplicate_is_fully_excluded():
    kept, _ = evidence._filter_rows([recent(), recent(homeTeamFullCourtGoalCnt="2")], {"10"}, "source", NOW)
    assert kept == []


def test_h2h_uses_sporttery_id_not_vendor_id():
    row = recent(homeTeamId=101, awayTeamId=202, sportteryHomeTeamId=10, sportteryAwayTeamId=20)
    kept, _ = evidence._filter_rows([row], {"10", "20"}, "source", NOW, h2h=True)
    assert len(kept) == 1
    row["sportteryHomeTeamId"] = 11
    kept, _ = evidence._filter_rows([row], {"10", "20"}, "source", NOW, h2h=True)
    assert kept == []


def test_one_source_outage_keeps_others(monkeypatch, tmp_path):
    def flaky(kind, *args, **kwargs):
        if kind == "recent":
            raise TimeoutError("timeout")
        return sources(kind, *args, **kwargs)
    monkeypatch.setattr(evidence, "fetch_evidence_source", flaky)
    result = evidence.collect_fixture_evidence(fixture(), output_dir=tmp_path, as_of=NOW)
    assert result["identity_verified"] and len(result["sources"]) == 4
    assert any("recent证据获取失败" in item for item in result["missing"])


def test_started_match_does_not_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "fetch_evidence_source", lambda *a, **kw: pytest.fail("must not fetch"))
    item = fixture()
    item.kickoff_time = NOW
    result = evidence.collect_fixture_evidence(item, output_dir=tmp_path, as_of=NOW)
    assert not result["identity_verified"]


@pytest.mark.parametrize("match_id", ["../123", "123&url=evil", "https://evil", "0", "abc"])
def test_source_rejects_untrusted_ids(tmp_path, match_id):
    with pytest.raises(ValueError):
        evidence.fetch_evidence_source("head", match_id, output_dir=tmp_path)


def test_transport_cache_preserves_fetch_time_and_canonical_hash(monkeypatch, tmp_path):
    payload = {"success": True, "errorCode": "0", "value": {"sportteryMatchId": 123}}
    calls = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return evidence.ROOT + "getMatchHeadV1.qry"
        def read(self, limit): return json.dumps(payload).encode()
    class Opener:
        def open(self, request, timeout):
            assert timeout == 12
            calls.append(request.full_url)
            return Reply()
    monkeypatch.setattr(evidence.urllib.request, "build_opener", lambda *a: Opener())
    _, first = evidence.fetch_evidence_source("head", "123", output_dir=tmp_path)
    _, cached = evidence.fetch_evidence_source("head", "123", output_dir=tmp_path)
    assert len(calls) == 1 and cached["cache_hit"]
    assert first["fetched_at"] == cached["fetched_at"]
    assert cached["source_updated_at"] is None
    assert first["sha256"] == hashlib.sha256(json.dumps(payload, ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    path = next((tmp_path/'cache').glob('*.json'))
    record = json.loads(path.read_text(encoding='utf8'))
    record['source']['fetched_at'] = (datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat()
    evidence.atomic_json_write(path,record)
    _, fresh = evidence.fetch_evidence_source("head","123",output_dir=tmp_path)
    assert len(calls) == 2 and not fresh['cache_hit']


def test_transport_rejects_official_looking_external_redirect(monkeypatch, tmp_path):
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return "https://sporttery.cn.evil.example/"
    class Opener:
        def open(self, *args, **kwargs): return Reply()
    monkeypatch.setattr(evidence.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(ValueError,match="官方域名"):
        evidence.fetch_evidence_source("head","123",output_dir=tmp_path)
