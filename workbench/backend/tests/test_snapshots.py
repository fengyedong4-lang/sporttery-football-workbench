from pathlib import Path
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.snapshots import fetch_sporttery_slate, import_official_snapshot, normalize_sporttery_slate
from app.schemas import FixtureBatch


def test_snapshot_keeps_missing_source_update_empty(tmp_path: Path):
    result = import_official_snapshot(
        {"value": []},
        source_url="https://webapi.sporttery.cn/example",
        redirect_chain=[],
        output_dir=tmp_path,
        source_updated_at=None,
    )
    assert result["source_updated_at"] is None
    assert (tmp_path / f"{result['snapshot_id']}.json").exists()


def test_snapshot_rejects_unofficial_redirect(tmp_path: Path):
    with pytest.raises(ValueError):
        import_official_snapshot(
            {}, source_url="https://sporttery.cn/a", redirect_chain=["https://evil.test/b"],
            output_dir=tmp_path, source_updated_at=None,
        )


def test_same_snapshot_does_not_overwrite_first_fetched_at(tmp_path: Path):
    first = import_official_snapshot(
        {"value": [1]}, source_url="https://webapi.sporttery.cn/example",
        redirect_chain=[], output_dir=tmp_path, source_updated_at=None,
    )
    second = import_official_snapshot(
        {"value": [1]}, source_url="https://webapi.sporttery.cn/example",
        redirect_chain=[], output_dir=tmp_path, source_updated_at=None,
    )
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["fetched_at"] == second["fetched_at"]
    assert len(list((tmp_path / "receipts").glob("*.json"))) >= 1


@pytest.fixture
def calculator_payload():
    def row(match_id, number, business_date, kickoff_date, kickoff_time):
        return {
            "matchId": match_id, "matchNumStr": number, "businessDate": business_date,
            "homeTeamAbbName": f"测试主队{match_id}", "awayTeamAbbName": f"测试客队{match_id}",
            "leagueAbbName": "测试杯", "matchDate": kickoff_date, "matchTime": kickoff_time,
            "had": {"h": "2.1", "d": "3.0", "a": "3.1"},
            "hhad": {"h": "4.1", "d": "3.5", "a": "1.8", "goalLine": "-1", "goalLineValue": "-1.00"},
            "poolList": [{"poolCode": "HAD", "poolStatus": "Selling"}, {"poolCode": "HHAD", "poolStatus": "Selling"}],
        }
    return {"success": True, "errorCode": "0", "value": {
        "totalCount": 6, "lastUpdateTime": "2026-09-22 13:14:09", "matchInfoList": [
            {"businessDate": "2026-09-22", "matchCount": 4, "subMatchList": [
                row(20, "周二001", "2026-09-22", "2026-09-22", "18:00:00"),
                row(21, "周二002", "2026-09-22", "2026-09-23", "02:00:00"),
                row(25, "周二003", "2026-09-22", "2026-09-23", "02:00:00"),
                row(22, "周二004", "2026-09-22", "2026-09-23", "02:00:00"),
            ]},
            {"businessDate": "2026-09-23", "matchCount": 2, "subMatchList": [
                row(23, "周三001", "2026-09-23", "2026-09-23", "13:30:00"),
                row(24, "周三002", "2026-09-23", "2026-09-23", "18:30:00"),
            ]},
        ],
    }}


def normalized(payload, date="2026-09-23", basis="all"):
    return normalize_sporttery_slate(payload, {
        "snapshot_id": "test-snapshot", "source_url": "https://webapi.sporttery.cn/example",
        "fetched_at": "2026-09-22T05:20:00+00:00", "observed_at": "2026-09-22T05:30:00+00:00",
        "source_updated_at": "2026-09-22 13:14:09",
    }, date, date_basis=basis)


def test_all_preserves_both_groups_dates_and_official_order(calculator_payload):
    result = normalized(calculator_payload)
    assert result["complete_for_scope"]
    assert result["match_count"] == result["source_match_count"] == result["source_reported_total"] == 6
    assert [f["match_id"] for f in result["fixtures"]] == ["20", "21", "25", "22", "23", "24"]
    assert [f["business_date"] for f in result["fixtures"]] == ["2026-09-22"] * 4 + ["2026-09-23"] * 2
    assert result["fixtures"][1]["kickoff_time"] == "2026-09-23T02:00:00+08:00"
    assert result["business_date"] is None  # Never assign a request date to all rows.
    FixtureBatch(fixtures=result["fixtures"])


@pytest.mark.parametrize(("basis", "date", "expected"), [
    ("business_date", "2026-09-22", ["20", "21", "25", "22"]),
    ("business_date", "2026-09-23", ["23", "24"]),
    ("kickoff_date", "2026-09-22", ["20"]),
    ("kickoff_date", "2026-09-23", ["21", "25", "22", "23", "24"]),
])
def test_numbering_day_and_beijing_kickoff_day_are_distinct(calculator_payload, basis, date, expected):
    result = normalized(calculator_payload, date, basis)
    assert [f["match_id"] for f in result["fixtures"]] == expected
    assert result["source_match_count"] == 6
    assert result["complete_for_scope"]
    FixtureBatch(fixtures=result["fixtures"])


def test_no_matching_day_is_not_claimed_as_a_complete_calendar(calculator_payload):
    result = normalized(calculator_payload, "2026-09-30", "business_date")
    assert result["fixtures"] == []
    assert not result["complete_for_business_date"]
    assert "不代表该日没有比赛" in result["warnings"][0]
    assert result["source_scope"] == "current_published_official_slate"


@pytest.mark.parametrize("which", ["group", "total"])
def test_reported_count_mismatch_blocks_completeness(calculator_payload, which):
    if which == "group":
        calculator_payload["value"]["matchInfoList"][0]["matchCount"] = 5
    else:
        calculator_payload["value"]["totalCount"] = 7
    result = normalized(calculator_payload)
    assert not result["complete_for_scope"]
    assert result["validation_errors"]
    assert result["match_count"] == 6


@pytest.mark.parametrize("error", ["duplicate", "missing", "date_conflict", "invalid_time", "invalid_odds"])
def test_invalid_row_is_explicit_and_cannot_be_claimed_complete(calculator_payload, error):
    row = calculator_payload["value"]["matchInfoList"][0]["subMatchList"][1]
    if error == "duplicate":
        row["matchId"] = 20
    elif error == "missing":
        row["homeTeamAbbName"] = ""
    elif error == "date_conflict":
        row["businessDate"] = "2026-09-23"
    elif error == "invalid_time":
        row["matchTime"] = "25:10:00"
    else:
        row["had"]["h"] = "NaN"
    result = normalized(calculator_payload)
    assert not result["complete_for_scope"]
    assert len(result["invalid_rows"]) == 1
    assert result["source_match_count"] == 6
    assert result["match_count"] == 5


def test_missing_had_stays_unoffered_not_invented(calculator_payload):
    row = calculator_payload["value"]["matchInfoList"][1]["subMatchList"][1]
    row["had"] = {}
    row["poolList"] = [{"poolCode": "HHAD", "poolStatus": "Selling"}]
    result = normalized(calculator_payload)
    assert result["fixtures"][-1]["result_play"] == {"status": "未开售", "home": None, "draw": None, "away": None}
    assert result["fixtures"][-1]["handicap_play"]["status"] == "销售中"


def test_missing_match_array_is_not_a_valid_empty_slate():
    with pytest.raises(ValueError, match="不能当作空赛单"):
        normalized({"value": {"totalCount": 0}})


def test_explicit_empty_official_slate():
    result = normalized({"value": {"totalCount": 0, "matchInfoList": []}})
    assert result["match_count"] == 0
    assert result["complete_for_scope"]
    assert result["warnings"]


def test_fetch_does_not_depend_on_ignored_server_date_filters(monkeypatch, calculator_payload, tmp_path):
    seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def geturl(self): return seen[-1]
        def read(self): return json.dumps(calculator_payload).encode("utf-8")
    class Opener:
        def open(self, request, timeout):
            seen.append(request.full_url)
            return Response()
    monkeypatch.setattr("app.services.snapshots.urllib.request.build_opener", lambda *args: Opener())
    result = fetch_sporttery_slate("2026-09-23", output_dir=tmp_path, date_basis="all")
    assert parse_qs(urlparse(seen[0]).query) == {"poolCode": ["hhad,had"], "channel": ["c"]}
    assert result["match_count"] == 6
    assert result["complete_for_scope"]


def test_slate_endpoint_passes_explicit_basis_and_preserves_legacy_default(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    calls = []
    def fake_fetch(business_date, *, output_dir, date_basis):
        calls.append((business_date, date_basis))
        return {"date_basis": date_basis}
    monkeypatch.setattr("app.main.fetch_sporttery_slate", fake_fetch)
    client = TestClient(app)
    for basis in ("all", "kickoff_date", None):
        body = {"business_date": "2026-09-23"}
        if basis:
            body["date_basis"] = basis
        assert client.post("/api/official/slate", json=body).status_code == 200
    assert calls == [("2026-09-23", "all"), ("2026-09-23", "kickoff_date"), ("2026-09-23", "business_date")]
    assert client.post("/api/official/slate", json={"business_date": "2026-09-23", "date_basis": "unknown"}).status_code == 422
