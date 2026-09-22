from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app import main, prediction_history_routes


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _settings(tmp_path):
    runtime = tmp_path / "runtime"
    history = tmp_path / "data" / "prediction_history.json"
    runtime.mkdir()
    history.parent.mkdir()
    return replace(main.settings, project_root=tmp_path, runtime_dir=runtime, history_path=history)


def _client(tmp_path, monkeypatch, history_records):
    settings = _settings(tmp_path)
    _write(settings.history_path, {"records": history_records})
    monkeypatch.setattr(prediction_history_routes, "settings", settings)
    return TestClient(main.app), settings


def test_complete_archive_preserves_versions_duplicates_unknowns_and_exports(tmp_path, monkeypatch):
    duplicate = {
        "prediction_date": "2026-01-01", "prediction_version": 1,
        "match_number": "周四001", "competition": "甲联赛", "home_team": "甲", "away_team": "乙",
    }
    client, settings = _client(tmp_path, monkeypatch, [duplicate, duplicate, {
        "prediction_date": "2026-01-01", "prediction_version": 2,
        "match_number": "周四001", "competition": "甲联赛", "home_team": "甲", "away_team": "乙",
    }, {"match_number": "旧001", "competition": "旧赛事", "home_team": "旧甲", "away_team": "旧乙"}])

    draft_id = "a" * 32
    _write(settings.runtime_dir / "drafts" / f"{draft_id}.json", {
        "run_id": draft_id, "created_at": "2026-01-01T08:00:00+08:00",
        "matches": [{"match_number": "草001", "competition": "草稿杯", "home_team": "丙", "away_team": "丁"}],
    })
    (settings.runtime_dir / "drafts" / ("b" * 32 + ".json")).write_text("broken", encoding="utf-8")

    _write(tmp_path / "exports" / "2026-01-01" / "prediction_V1.json", {
        "prediction_date": "2026-01-01", "prediction_version": 1, "state": "formal",
        "matches": [
            {"match_number": "存001", "competition": "存档杯", "home_team": "戊", "away_team": "己"},
            {"match_number": "存002", "competition": "存档杯", "home_team": "庚", "away_team": "辛"},
        ],
    })
    _write(tmp_path / "exports" / "2026-01-02" / "prediction_V2.json", {
        "schema_version": 4, "prediction_sets": [
            {"prediction_date": "2026-01-02", "prediction_version": 1, "matches": [
                {"match_number": "集001", "competition": "集合杯", "home_team": "壬", "away_team": "癸"},
            ]},
            {"prediction_date": "2026-01-02", "prediction_version": 2, "records": [
                {"match_number": "集001", "competition": "集合杯", "home_team": "壬", "away_team": "癸"},
                {"match_number": "集002", "competition": "集合杯", "home_team": "子", "away_team": "丑"},
            ]},
        ],
    })
    _write(tmp_path / "exports" / "baseline_backup" / "prediction_V9.json", {
        "matches": [{"competition": "不应出现"}],
    })

    response = client.get("/api/prediction-history", params={"page_size": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {
        "draft_batches": 1, "formal_batches": 3, "formal_records": 4,
        "export_batches": 3, "export_records": 5,
    }
    assert body["total"] == 7 and len(body["items"]) == 7
    assert any("JSONDecodeError" in warning for warning in body["warnings"])

    formal_v1 = next(item for item in body["items"] if item["kind"] == "formal" and "V1" in item["label"])
    detail = client.get(f"/api/prediction-history/formal/{formal_v1['id']}").json()
    assert detail["matches"] == [duplicate, duplicate]
    assert detail["raw"]["records"] == [duplicate, duplicate]
    assert detail["read_only"] is True
    unknown = next(item for item in body["items"] if item["kind"] == "formal" and "未知日期" in item["label"])
    assert client.get(f"/api/prediction-history/formal/{unknown['id']}").json()["match_count"] == 1

    exports = [item for item in body["items"] if item["kind"] == "export"]
    nested = next(item for item in exports if "集合2" in item["label"])
    nested_detail = client.get(f"/api/prediction-history/export/{nested['id']}").json()
    assert nested_detail["match_count"] == 2
    assert nested_detail["raw"]["schema_version"] == 4
    assert nested_detail["raw"]["prediction_sets"][1]["records"] == nested_detail["matches"]
    assert nested_detail["source_set_index"] == 1
    assert nested_detail["source_path"] == "exports/2026-01-02/prediction_V2.json"


def test_pagination_search_competition_and_beijing_dates(tmp_path, monkeypatch):
    client, settings = _client(tmp_path, monkeypatch, [{
        "prediction_date": "2026-01-01", "prediction_version": 1,
        "match_number": "周四001", "competition": "测试联赛", "home_team": "甲队", "away_team": "乙队",
    }, {
        "prediction_date": "2026-01-02", "prediction_version": 1,
        "match_number": "周五001", "competition": "其他杯", "home_team": "丙队", "away_team": "丁队",
    }])
    draft_id = "c" * 32
    _write(settings.runtime_dir / "drafts" / f"{draft_id}.json", {
        "run_id": draft_id, "created_at": "2026-01-01T15:55:00Z", "completed_at": "2026-01-01T16:30:00Z",
        "matches": [
            {"match_number": "草001", "competition": "测试联赛", "home_team": "目标队", "away_team": "对手"},
            {"match_number": "草002", "competition": "完整批次杯", "home_team": "另一队", "away_team": "另一对手"},
        ],
    })

    searched = client.get("/api/prediction-history", params={"q": "目标队"}).json()
    assert searched["total"] == 1 and searched["items"][0]["kind"] == "draft"
    detail = client.get(f"/api/prediction-history/draft/{draft_id}").json()
    assert detail["match_count"] == 2  # A match selects a batch; detail stays complete.

    by_competition = client.get("/api/prediction-history", params={"competition": "测试联赛", "page_size": 1}).json()
    assert by_competition["total"] == 2 and by_competition["total_pages"] == 2 and len(by_competition["items"]) == 1
    assert client.get("/api/prediction-history", params={"competition": "测试联"}).json()["total"] == 0

    beijing_day = client.get("/api/prediction-history", params={
        "kind": "draft", "date_from": "2026-01-02", "date_to": "2026-01-02",
    }).json()
    assert beijing_day["total"] == 1
    assert beijing_day["items"][0]["prediction_date"] == "2026-01-02"
    assert beijing_day["items"][0]["created_at"] == "2026-01-01T16:30:00Z"
    assert client.get("/api/prediction-history", params={
        "date_from": "2026-01-03", "date_to": "2026-01-02",
    }).status_code == 422


def test_ids_are_opaque_path_safe_and_requests_do_not_write(tmp_path, monkeypatch):
    record = {
        "prediction_date": "2026-01-01", "prediction_version": 1,
        "match_number": "周四001", "competition": "测试联赛", "home_team": "甲", "away_team": "乙",
    }
    client, settings = _client(tmp_path, monkeypatch, [record])
    draft_id = "d" * 32
    draft_path = settings.runtime_dir / "drafts" / f"{draft_id}.json"
    _write(draft_path, {"run_id": draft_id, "created_at": "2026-01-01T08:00:00+08:00", "matches": [record]})
    export_path = tmp_path / "exports" / "2026-01-01" / "prediction_V1.json"
    _write(export_path, {"prediction_date": "2026-01-01", "prediction_version": 1, "matches": [record]})
    protected = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (settings.history_path, draft_path, export_path)}

    listing = client.get("/api/prediction-history", params={"page_size": 100}).json()
    assert all(set(item["id"]) <= set("0123456789abcdef") for item in listing["items"])
    assert client.get("/api/prediction-history/draft/not-an-id").status_code == 404
    assert client.get("/api/prediction-history/formal/..%2Fprediction_history.json").status_code in {404, 422}
    formal = next(item for item in listing["items"] if item["kind"] == "formal")
    assert client.get(f"/api/prediction-history/formal/{formal['id']}").status_code == 200

    for path, (content, mtime) in protected.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime
