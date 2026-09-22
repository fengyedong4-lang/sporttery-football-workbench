from pathlib import Path
from datetime import date, timedelta

import pytest

from app.services.backtest import compare_backtests, save_backtest, walk_forward_backtest
from app.services.model import GoalModel, ModelError, TrainingMatch, UnknownTeamError, import_training_csv


@pytest.mark.parametrize("model_type", ["poisson", "dixon_coles"])
def test_model_trains_predicts_and_reloads(training_matches, tmp_path: Path, model_type: str):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type=model_type)
    prediction = model.predict("A", "B")
    assert prediction["tail_error"] <= 1e-8
    assert abs(sum(prediction["difference_probabilities"].values()) - 1) < 1e-10
    output = tmp_path / "model.json"
    model.save(output)
    loaded = GoalModel.load(output)
    assert loaded.model_type == model_type
    assert loaded.predict("A", "B")["tail_error"] <= 1e-8


def test_unknown_team_and_insufficient_data_do_not_fake_output(training_matches):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    with pytest.raises(UnknownTeamError):
        model.predict("A", "UNKNOWN")
    with pytest.raises(ModelError):
        GoalModel.fit(training_matches[:3], competition="测试联赛", model_type="poisson")


def test_training_import_deduplicates_versions(tmp_path: Path):
    csv_path = tmp_path / "training.csv"
    csv_path.write_text(
        "match_id,competition,kickoff_date,home_team,away_team,home_goals_90,away_goals_90,neutral,status,source\n"
        "x,L,2025-01-01,A,B,1,0,false,completed_90,s\n"
        "x,L,2025-01-01,A,B,1,0,false,completed_90,s\n"
        "y,L,2025-01-02,C,D,2,2,false,cancelled,s\n",
        encoding="utf-8",
    )
    rows = import_training_csv(csv_path)
    assert [row.match_id for row in rows] == ["x"]


def test_walk_forward_has_no_future_leakage(training_matches):
    training_matches[13] = training_matches[13].__class__(
        **{**training_matches[13].__dict__, "kickoff_date": training_matches[12].kickoff_date}
    )
    report = walk_forward_backtest(
        training_matches, competition="测试联赛", model_type="poisson", min_train=12, refit_every=4
    )
    assert report["no_future_leakage"] is True
    assert report["denominator"] > 0
    assert report["handicap_evaluation"].startswith("资料不足")


def test_model_comparison_uses_same_sample(tmp_path: Path):
    baseline = {"model_type": "poisson", "competition": "L", "predictions": [
        {"match_id": "a", "hit": True, "brier": .1, "log_loss": .5},
        {"match_id": "b", "hit": False, "brier": .3, "log_loss": 1.2},
    ]}
    candidate = {"model_type": "dixon_coles", "competition": "L", "predictions": [
        {"match_id": "b", "hit": True, "brier": .2, "log_loss": .8},
        {"match_id": "c", "hit": True, "brier": .1, "log_loss": .4},
    ]}
    baseline_path, candidate_path = tmp_path / "b.json", tmp_path / "c.json"
    save_backtest(baseline, baseline_path); save_backtest(candidate, candidate_path)
    result = compare_backtests(baseline_path, candidate_path)
    assert result["same_valid_sample"] is True
    assert result["common_sample_count"] == 1
    assert result["baseline"]["denominator"] == result["candidate"]["denominator"] == 1


def test_model_learns_global_goal_baseline_on_neutral_matches():
    teams = ["A", "B", "C", "D"]
    rows = []
    index = 0
    for home in teams:
        for away in teams:
            if home == away:
                continue
            rows.append(TrainingMatch(
                match_id=str(index), competition="中立测试", kickoff_date=date(2025, 1, 1) + timedelta(days=index),
                home_team=home, away_team=away, home_goals=2, away_goals=2,
                neutral=True, source="test",
            ))
            index += 1
    model = GoalModel.fit(rows, competition="中立测试", model_type="poisson")
    prediction = model.predict("A", "B", neutral=True)
    assert prediction["home_rate"] == pytest.approx(2.0, rel=0.03)
    assert prediction["away_rate"] == pytest.approx(2.0, rel=0.03)
