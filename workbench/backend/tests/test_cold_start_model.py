from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.cold_start_model import ColdStartModel, temporal_evaluate
from app.services.model import ModelError
from app.services.play_logic import aggregate_difference_probabilities


CHINA = timezone(timedelta(hours=8), "Asia/Shanghai")


def _row(
    match_id: str,
    day: str,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
    **updates,
):
    row = {
        "match_id": match_id,
        "match_date": day,
        "home_team_id": home,
        "away_team_id": away,
        "home_goals_90": home_goals,
        "away_goals_90": away_goals,
        "competition_id": "comp-1",
        "season_id": "2026",
        "status": "completed_90",
        "score_basis": "90_minutes",
        "source_id": "source-a",
        "is_friendly": False,
    }
    row.update(updates)
    return row


def test_zero_samples_raise_and_one_to_three_are_baseline_only():
    cutoff = datetime(2026, 1, 5, 12, tzinfo=CHINA)
    with pytest.raises(ModelError):
        ColdStartModel.fit([], competition_id="comp-1", season_id="2026", cutoff=cutoff)

    model = ColdStartModel.fit(
        [_row("m1", "2026-01-01", "A", "B", 2, 0)],
        competition_id="comp-1",
        season_id="2026",
        cutoff=cutoff,
    )
    assert model.fit_status == "baseline_only_prior_dominated"
    assert model.baseline_rate == pytest.approx(1.25)

    both_unknown = model.predict("X", "Y")
    one_unknown = model.predict("A", "X")
    assert both_unknown["home_rate"] == pytest.approx(model.baseline_rate)
    assert both_unknown["away_rate"] == pytest.approx(model.baseline_rate)
    assert both_unknown["unknown_teams"] == ["X", "Y"]
    assert both_unknown["prior_dominated"] is True
    assert one_unknown["sample_counts"]["home_team_matches"] == 1
    assert one_unknown["sample_counts"]["away_team_matches"] == 0


def test_filtering_cutoff_scores_scope_and_conflicting_duplicates():
    rows = [
        _row("valid", "2026-01-01", "A", "B", 1, 0),
        _row("same", "2026-01-02", "B", "C", 2, 1),
        _row("same", "2026-01-02", "B", "C", 2, 1, source_id="source-b"),
        _row("conflict", "2026-01-03", "C", "A", 1, 1),
        _row("conflict", "2026-01-03", "C", "A", 2, 1),
        _row("other-comp", "2026-01-01", "A", "B", 8, 8, competition_id="comp-2"),
        _row("other-season", "2026-01-01", "A", "B", 8, 8, season_id="2025"),
        _row("same-day", "2026-01-05", "A", "B", 8, 8),
        _row("future", "2026-01-06", "A", "B", 8, 8),
        _row("negative", "2026-01-02", "A", "B", -1, 0),
        _row("nan", "2026-01-02", "A", "B", float("nan"), 0),
        _row("bool", "2026-01-02", "A", "B", True, 0),
        _row("missing-team", "2026-01-02", "", "B", 1, 0),
        _row("friendly", "2026-01-02", "A", "B", 1, 0, is_friendly=True),
    ]
    model = ColdStartModel.fit(
        rows,
        competition_id="comp-1",
        season_id="2026",
        cutoff=datetime(2026, 1, 5, 23, 59, tzinfo=CHINA),
    )
    assert model.training_match_count == 2
    assert model.excluded_rows["identical_duplicate_rows"] == 1
    assert model.excluded_rows["conflicting_duplicate_rows"] == 2
    assert model.excluded_rows["on_or_after_cutoff_date"] == 2
    assert model.excluded_rows["invalid_score"] == 3
    assert model.excluded_rows["other_competition"] == 1
    assert model.excluded_rows["other_season"] == 1


def test_regularized_fit_probability_normalization_and_two_play_aggregation():
    rows = []
    start = datetime(2026, 1, 1)
    teams = ["A", "B", "C", "D"]
    for index in range(24):
        home = teams[index % 4]
        away = teams[(index + 1 + index // 4) % 4]
        if home == away:
            away = teams[(teams.index(home) + 1) % 4]
        rows.append(
            _row(
                f"m{index}",
                (start + timedelta(days=index)).date().isoformat(),
                home,
                away,
                3 if home == "A" else index % 2,
                0 if away == "A" else (index + 1) % 2,
            )
        )
    model = ColdStartModel.fit(
        rows,
        competition_id="comp-1",
        season_id="2026",
        cutoff=datetime(2026, 2, 1, tzinfo=CHINA),
    )
    assert model.fit_status == "regularized_poisson_map"
    assert model.optimizer["regularization_strength"] == 4.0
    assert model.optimizer["analytic_gradient"] is True

    prediction = model.predict("A", "new-team")
    assert abs(sum(prediction["difference_probabilities"].values()) - 1.0) < 1e-12
    assert prediction["tail_error"] <= 1e-10
    assert prediction["unknown_teams"] == ["new-team"]
    result, handicap_result = aggregate_difference_probabilities(
        prediction["difference_probabilities"], -1
    )
    assert sum(result.values()) == pytest.approx(1.0)
    assert sum(handicap_result.values()) == pytest.approx(1.0)
    assert set(result) == {"胜", "平", "负"}
    assert set(handicap_result) == {"让胜", "让平", "让负"}

    artifact = model.to_dict()
    assert artifact["home_advantage_modelled"] is False
    assert artifact["cross_competition_transfer"] is False
    assert artifact["odds_used"] is False
    json.dumps(artifact, ensure_ascii=False)


def test_prior_only_ignores_known_team_parameters_and_cutoff_uses_china_date():
    rows = []
    for index in range(12):
        rows.append(
            _row(
                f"m{index}",
                f"2026-01-{index + 1:02d}",
                "A" if index % 2 == 0 else "B",
                "C" if index % 2 == 0 else "D",
                4 if index % 2 == 0 else 0,
                0 if index % 2 == 0 else 2,
            )
        )
    # 2026-01-13 00:30 in China is still 2026-01-12 in UTC; Jan 12 remains a prior day.
    cutoff = datetime.fromisoformat("2026-01-12T16:30:00+00:00")
    model = ColdStartModel.fit(
        rows,
        competition_id="comp-1",
        season_id="2026",
        cutoff=cutoff,
    )
    assert model.training_match_count == 12
    default = model.predict("A", "C")
    prior = model.predict("A", "C", prior_only=True)
    assert default["home_rate"] != pytest.approx(model.baseline_rate)
    assert prior["home_rate"] == pytest.approx(model.baseline_rate)
    assert prior["away_rate"] == pytest.approx(model.baseline_rate)
    assert prior["prior_only"] is True
    assert prior["prior_dominated"] is True
    assert prior["prior_dominated_teams"] == ["A", "C"]

    with pytest.raises(ModelError, match="时区"):
        ColdStartModel.fit(
            rows,
            competition_id="comp-1",
            season_id="2026",
            cutoff=datetime(2026, 2, 1),
        )


def test_temporal_evaluation_uses_only_strictly_earlier_dates():
    start = datetime(2026, 1, 1)
    rows = []
    for index in range(12):
        day = (start + timedelta(days=index)).date().isoformat()
        rows.append(
            _row(
                f"m{index}",
                day,
                ["A", "B", "C"][index % 3],
                ["B", "C", "A"][index % 3],
                index % 3,
                (index + 1) % 2,
            )
        )
    # A second match on an evaluation date must remain test-only for that entire date group.
    rows.append(_row("same-date-extra", "2026-01-08", "D", "A", 1, 1))

    report = temporal_evaluate(rows, "comp-1", "2026", min_train=4)
    assert report["status"] == "ok"
    assert report["denominator"] > 0
    assert report["baseline"]["denominator"] == report["denominator"]
    assert report["no_future_leakage"] is True
    assert report["same_day_training_excluded"] is True
    assert 0 <= report["accuracy"] <= 1
    for fold in report["folds"]:
        assert fold["latest_training_date"] < fold["test_date"]


def test_temporal_evaluation_reports_not_enough_data():
    rows = [
        _row("m1", "2026-01-01", "A", "B", 1, 0),
        _row("m2", "2026-01-01", "C", "D", 0, 0),
    ]
    report = temporal_evaluate(rows, "comp-1", "2026", min_train=2)
    assert report["status"] == "not_enough_data"
    assert report["denominator"] == 0
    assert report["reason"] == "no_date_has_required_strictly_earlier_training_prefix"
