from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime

import pytest

from app.schemas import Fixture, OfficialPlay
from app.services.evidence_analysis import analyze_evidence


AS_OF = datetime.fromisoformat("2026-09-20T12:00:00+08:00")


def fixture(*, handicap: int | None = -1, odds: float = 2.0) -> Fixture:
    return Fixture(
        sequence=1,
        match_id="fixture-1",
        match_number="周日001",
        business_date="2026-09-20",
        competition="测试杯",
        home_team="主队",
        away_team="客队",
        kickoff_time=datetime.fromisoformat("2026-09-21T20:00:00+08:00"),
        official_handicap=handicap,
        result_play=OfficialPlay(status="销售中", home=odds, draw=3.0, away=4.0),
        handicap_play=OfficialPlay(status="销售中", home=odds, draw=3.0, away=4.0),
    )


def row(
    match_id: str,
    match_date: str,
    home_id: str,
    away_id: str,
    home_goals: int,
    away_goals: int,
    *,
    competition_id: str = "league",
    season_id: str = "2026",
    source_id: str = "source-1",
    friendly: bool = False,
) -> dict:
    return {
        "match_id": match_id,
        "kickoff_time": None,
        "match_date": match_date,
        "time_precision": "date",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_goals_90": home_goals,
        "away_goals_90": away_goals,
        "competition": "测试联赛",
        "competition_id": competition_id,
        "season_id": season_id,
        "status": "completed_90",
        "score_basis": "90_minutes",
        "is_friendly": friendly,
        "source_id": source_id,
    }


def bundle(home_rows: list[dict], away_rows: list[dict]) -> dict:
    return {
        "identity_verified": True,
        "collected_at": "2026-09-20T11:00:00+08:00",
        "sources": [
            {
                "source_id": "source-1",
                "url": "https://example.test/results",
                "fetched_at": "2026-09-20T11:00:00+08:00",
                "source_updated_at": "2026-09-20T10:00:00+08:00",
                "sha256": "a" * 64,
            }
        ],
        "teams": {
            "home": {"team_id": "H", "name": "主队", "recent_matches": home_rows},
            "away": {"team_id": "A", "name": "客队", "recent_matches": away_rows},
        },
        "missing": [],
        "warnings": [],
        "facts": [],
    }


def comparable_rows() -> tuple[list[dict], list[dict]]:
    home = [
        row("h1", "2026-09-18", "H", "x1", 2, 0),
        row("h2", "2026-09-12", "x2", "H", 1, 1),
        row("h3", "2026-09-05", "H", "x3", 1, 0),
        row("h4", "2026-08-28", "x4", "H", 1, 2),
    ]
    away = [
        row("a1", "2026-09-17", "x5", "A", 0, 1),
        row("a2", "2026-09-11", "A", "x6", 1, 1),
        row("a3", "2026-09-03", "x7", "A", 2, 0),
        row("a4", "2026-08-26", "A", "x8", 0, 1),
    ]
    return home, away


def test_filters_unknown_team_future_duplicate_friendly_old_and_unclear_90_rows() -> None:
    home, away = comparable_rows()
    home.extend(
        [
            row("wrong-team", "2026-09-10", "not-H", "x", 8, 0),
            row("same-day", "2026-09-20", "H", "x", 8, 0),
            row("future", "2026-09-21", "H", "x", 8, 0),
            row("old", "2026-01-01", "H", "x", 8, 0),
            row("friendly", "2026-09-10", "H", "x", 8, 0, friendly=True),
            row("h1", "2026-09-18", "H", "x1", 2, 0),
        ]
    )
    unclear = row("unclear", "2026-09-10", "H", "x", 8, 0)
    unclear["score_basis"] = "including_extra_time"
    home.append(unclear)

    result = analyze_evidence(fixture(), bundle(home, away), as_of=AS_OF)

    assert result["analysis_status"] == "available"
    assert result["evidence_stats"]["home"]["sample_size"] == 4
    assert result["evidence_stats"]["home"]["goals_for"] == 6
    assert result["matched_evidence_ids"] == ["source-1"]


def test_sparse_exact_ids_do_not_backfill_old_or_other_team_samples() -> None:
    home = [row("h1", "2026-09-18", "H", "x", 1, 0)]
    away = [row("a1", "2026-09-18", "x", "A", 0, 0)]
    home.extend(row(f"other-{i}", "2026-09-10", "old-H", f"x{i}", 5, 0) for i in range(8))

    result = analyze_evidence(fixture(), bundle(home, away), as_of=AS_OF)

    assert result["analysis_status"] == "insufficient_evidence"
    assert result["raw_probabilities"] is None
    assert result["analysis_result"] is None
    assert result["evidence_stats"]["home"]["sample_size"] == 1
    assert any("换代" in item for item in result["major_counterevidence"])


def test_no_common_competition_season_is_cross_level_and_not_compared() -> None:
    home, away = comparable_rows()
    for item in away:
        item["competition_id"] = "lower-league"

    result = analyze_evidence(fixture(), bundle(home, away), as_of=AS_OF)

    assert result["analysis_status"] == "insufficient_evidence"
    assert result["method_parameters"]["comparison_status"] == "cross_level"
    assert result["raw_probabilities"] is None
    assert any("竞赛层级" in item for item in result["major_counterevidence"])


def test_available_probabilities_are_finite_normalized_and_do_not_leak_scores() -> None:
    home, away = comparable_rows()
    result = analyze_evidence(fixture(), bundle(home, away), as_of=AS_OF)

    assert result["analysis_status"] == "available"
    for probabilities in result["raw_probabilities"].values():
        assert probabilities is not None
        assert sum(probabilities.values()) == pytest.approx(1.0)
        assert all(math.isfinite(value) and 0 <= value <= 1 for value in probabilities.values())
    encoded = json.dumps(result, ensure_ascii=False)
    assert '"predicted_score"' not in encoded
    assert '"total_goals"' not in encoded


def test_zero_rates_are_valid_and_not_replaced_with_arbitrary_defaults() -> None:
    home = [row(f"h{i}", f"2026-09-0{i}", "H", f"x{i}", 0, 0) for i in range(1, 5)]
    away = [row(f"a{i}", f"2026-09-0{i}", f"y{i}", "A", 0, 0) for i in range(1, 5)]

    result = analyze_evidence(fixture(handicap=None), bundle(home, away), as_of=AS_OF)

    assert result["method_parameters"]["lambda_home"] == 0
    assert result["method_parameters"]["lambda_away"] == 0
    assert result["raw_probabilities"]["result"] == {"胜": 0.0, "平": 1.0, "负": 0.0}
    assert result["raw_probabilities"]["handicap_result"] is None
    assert result["analysis_handicap_result"] is None


def test_odds_changes_do_not_affect_evidence_analysis() -> None:
    home, away = comparable_rows()
    evidence = bundle(home, away)

    low_odds = analyze_evidence(fixture(odds=1.01), deepcopy(evidence), as_of=AS_OF)
    high_odds = analyze_evidence(fixture(odds=99.0), deepcopy(evidence), as_of=AS_OF)

    fields = ["analysis_result", "analysis_handicap_result", "raw_probabilities", "confidence"]
    assert {key: low_odds[key] for key in fields} == {key: high_odds[key] for key in fields}


def test_naive_times_and_started_fixture_block_all_options() -> None:
    home, away = comparable_rows()
    naive = analyze_evidence(fixture(), bundle(home, away), as_of=datetime(2026, 9, 20, 12))
    started = analyze_evidence(
        fixture(),
        bundle(home, away),
        as_of=datetime.fromisoformat("2026-09-21T20:00:00+08:00"),
    )

    for result in (naive, started):
        assert result["analysis_status"] == "blocked"
        assert result["analysis_result"] is None
        assert result["analysis_handicap_result"] is None
        assert result["raw_probabilities"] is None


def test_handicap_is_withheld_when_top_picks_are_incompatible(monkeypatch: pytest.MonkeyPatch) -> None:
    home, away = comparable_rows()

    def fake_aggregate(_differences: dict[int, float], _handicap: int | None):
        return (
            {"胜": 0.60, "平": 0.25, "负": 0.15},
            {"让胜": 0.10, "让平": 0.20, "让负": 0.70},
        )

    monkeypatch.setattr("app.services.evidence_analysis.aggregate_difference_probabilities", fake_aggregate)
    result = analyze_evidence(fixture(handicap=1), bundle(home, away), as_of=AS_OF)

    assert result["analysis_result"] == "胜"
    assert result["analysis_handicap_result"] is None
    assert any("不相容" in item for item in result["major_counterevidence"])


def test_let_draw_requires_probability_margin_and_two_real_boundary_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, away = comparable_rows()

    def let_draw_top(_differences: dict[int, float], _handicap: int | None):
        return (
            {"胜": 0.60, "平": 0.22, "负": 0.18},
            {"让胜": 0.25, "让平": 0.50, "让负": 0.25},
        )

    monkeypatch.setattr("app.services.evidence_analysis.aggregate_difference_probabilities", let_draw_top)
    result = analyze_evidence(fixture(handicap=-3), bundle(home, away), as_of=AS_OF)

    assert result["method_parameters"]["handicap_exact_boundary_paths"] < 2
    assert result["analysis_handicap_result"] is None
    assert any("精确净胜球边界" in item for item in result["major_counterevidence"])


def test_let_draw_can_survive_only_with_margin_paths_and_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, away = comparable_rows()
    home[0]["home_goals_90"], home[0]["away_goals_90"] = 1, 0
    home[2]["home_goals_90"], home[2]["away_goals_90"] = 1, 0

    def let_draw_top(_differences: dict[int, float], _handicap: int | None):
        return (
            {"胜": 0.60, "平": 0.22, "负": 0.18},
            {"让胜": 0.25, "让平": 0.50, "让负": 0.25},
        )

    monkeypatch.setattr("app.services.evidence_analysis.aggregate_difference_probabilities", let_draw_top)
    result = analyze_evidence(fixture(handicap=-1), bundle(home, away), as_of=AS_OF)

    assert result["method_parameters"]["handicap_exact_boundary_paths"] >= 2
    assert result["analysis_result"] == "胜"
    assert result["analysis_handicap_result"] == "让平"


def test_draw_top_without_clear_balance_is_left_uncertain(monkeypatch: pytest.MonkeyPatch) -> None:
    home, away = comparable_rows()

    def draw_top(_differences: dict[int, float], _handicap: int | None):
        return (
            {"胜": 0.25, "平": 0.50, "负": 0.25},
            {"让胜": 0.60, "让平": 0.20, "让负": 0.20},
        )

    monkeypatch.setattr("app.services.evidence_analysis.aggregate_difference_probabilities", draw_top)
    result = analyze_evidence(fixture(handicap=-1), bundle(home, away), as_of=AS_OF)

    if not result["method_parameters"]["draw_balance_supported"]:
        assert result["analysis_result"] is None
        assert any("攻防均衡" in item for item in result["major_counterevidence"])
