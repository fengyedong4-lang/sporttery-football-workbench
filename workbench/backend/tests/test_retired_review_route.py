import pytest
from fastapi.testclient import TestClient

from app import main, review_routes


@pytest.mark.parametrize("payload", [
    {},
    {"prediction_path": "drafts/postgame.json", "results": [
        {"match_id": "2041642", "status": "已完赛", "home_goals_90": 2,
         "away_goals_90": 0, "verified_90_minutes": True,
         "official_settlement_verified": True}
    ]},
    {"prediction_path": "../../data/prediction_history.json", "results": []},
])
def test_legacy_route_is_gone_before_any_read_or_scoring(monkeypatch, payload):
    def forbidden(*args, **kwargs):
        pytest.fail("retired endpoint must not read, score or write")
    monkeypatch.setattr(main, "safe_child", forbidden)
    monkeypatch.setattr(main, "settle_review", forbidden)
    from app.services import history
    monkeypatch.setattr(history, "atomic_json_write", forbidden)
    response = TestClient(main.app).post("/api/reviews/settle", json=payload)
    assert response.status_code == 410
    assert "/api/manual-reviews/run" in response.json()["detail"]
    assert "hits" not in response.json()


def test_manual_review_route_remains_available(monkeypatch):
    calls = []
    def run(settings, batch_id, *, use_astra):
        calls.append((batch_id, use_astra))
        return {"status": "partial_pending", "statistics": {"combined": {"hits": 0, "denominator": 0}}}
    monkeypatch.setattr(review_routes.service, "run_review", run)
    response = TestClient(main.app).post("/api/manual-reviews/run", json={"batch_id": "effective:2026-09-22", "use_astra": False})
    assert response.status_code == 200
    assert calls == [("effective:2026-09-22", False)]
    assert response.json()["statistics"]["combined"]["denominator"] == 0
