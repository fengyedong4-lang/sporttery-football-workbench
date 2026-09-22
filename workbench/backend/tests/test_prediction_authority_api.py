from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from test_fixture_authority import bound
from test_snapshots import calculator_payload


def test_tampered_handicap_is_rejected_before_analysis(tmp_path, calculator_payload, monkeypatch):
    fixtures, _ = bound(tmp_path, calculator_payload)
    fixture = fixtures[0].model_dump(mode="json")
    fixture["official_handicap"] = 100
    monkeypatch.setattr(main, "settings", SimpleNamespace(runtime_dir=tmp_path))
    monkeypatch.setattr(main, "run_daily_prediction", lambda *a, **k: (_ for _ in ()).throw(AssertionError("analysis must not run")))
    response = TestClient(main.app).post("/api/predictions/daily", json={"fixtures": [fixture]})
    assert response.status_code == 400
    assert "official_handicap" in response.json()["detail"]


def test_manual_market_is_downgraded_at_http_boundary(tmp_path, calculator_payload, monkeypatch):
    fixtures, _ = bound(tmp_path, calculator_payload)
    fixture = fixtures[0].model_dump(mode="json")
    fixture["source_snapshot_id"] = None
    monkeypatch.setattr(main, "settings", SimpleNamespace(
        runtime_dir=tmp_path, rules_path=tmp_path / "rules", rule_index_path=tmp_path / "index",
        llm_enabled=False, llm_model="test", llm_effort="medium", llm_timeout_seconds=30,
        dynamic_research_enabled=False,
    ))
    monkeypatch.setattr(main, "RulePrefilter", lambda *a: None)
    monkeypatch.setattr(main, "load_active_model_routing", lambda *a: ({}, {}, {}))
    monkeypatch.setattr(main, "run_daily_prediction", lambda items, **k: {"fixtures": [f.model_dump(mode="json") for f in items]})
    response = TestClient(main.app).post("/api/predictions/daily", json={"fixtures": [fixture]})
    assert response.status_code == 200
    row = response.json()["fixtures"][0]
    assert row["official_handicap"] is None
    assert row["result_play"] == row["handicap_play"] == {"status": "待核实", "home": None, "draw": None, "away": None}
