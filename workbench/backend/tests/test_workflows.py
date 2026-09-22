from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas import Fixture, OfficialPlay, ResultInput
from app.services.model import GoalModel
from app.services.play_logic import aggregate_difference_probabilities
from app.services.workflows import run_daily_prediction, settle_review


class EmptyPrefilter:
    def match(self, context, budget=6):
        return []


def fixture(sequence: int, competition: str, handicap: int | None = -1) -> Fixture:
    return Fixture(
        sequence=sequence,
        match_id=f"id-{sequence}",
        match_number=f"00{sequence}",
        business_date="2025-02-01",
        competition=competition,
        home_team="A",
        away_team="B",
        kickoff_time=datetime.now(timezone.utc) + timedelta(days=1),
        official_handicap=handicap,
        result_play=OfficialPlay(status="销售中", home=2.0, draw=3.0, away=4.0),
        handicap_play=OfficialPlay(status="销售中" if handicap is not None else "待核实", home=2.0, draw=3.0, away=4.0),
    )


def test_predict_all_preserves_rows_and_missing_handicap(training_matches, tmp_path: Path):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    model_path = tmp_path / "m.json"
    model.save(model_path)
    result = run_daily_prediction(
        [fixture(1, "测试联赛", None), fixture(2, "未覆盖联赛")],
        model_path=model_path,
        prefilter=EmptyPrefilter(),
        rule_budget=5,
        output_dir=tmp_path,
    )
    assert len(result["matches"]) == 2
    assert result["matches"][0]["official_handicap"] is None
    assert result["matches"][0]["handicap_result"] is None
    assert result["matches"][1]["model_status"] == "unavailable"
    assert result["official_fetch_performed"] is False
    assert result["llm_calls"] == 0


def test_auto_model_mapping_routes_each_competition(training_matches, tmp_path: Path):
    first = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    first_path = tmp_path / "first.json"
    first.save(first_path)
    second_rows = [type(row)(**{**row.__dict__, "competition": "另一个联赛"}) for row in training_matches]
    second = GoalModel.fit(second_rows, competition="另一个联赛", model_type="poisson")
    second_path = tmp_path / "second.json"
    second.save(second_path)
    result = run_daily_prediction(
        [fixture(1, "测试联赛"), fixture(2, "另一个联赛")],
        model_paths={"测试联赛": first_path, "另一个联赛": second_path},
        prefilter=EmptyPrefilter(), rule_budget=5, output_dir=tmp_path,
    )
    assert [row["model_version"] for row in result["matches"]] == ["first", "second"]


def test_explicit_team_alias_is_used_without_fuzzy_guess(training_matches, tmp_path: Path):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    path = tmp_path / "alias.json"; model.save(path)
    item = fixture(1, "测试联赛")
    item.home_team = "主队中文"; item.away_team = "客队中文"
    result = run_daily_prediction(
        [item], model_paths={"测试联赛": path},
        team_aliases={"测试联赛": {"主队中文": "A", "客队中文": "B"}},
        prefilter=EmptyPrefilter(), rule_budget=5, output_dir=tmp_path,
    )
    assert result["matches"][0]["resolved_teams"] == {"home": "A", "away": "B"}


def test_selling_without_all_three_odds_is_not_formal_selection(training_matches, tmp_path: Path):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    path = tmp_path / "m.json"; model.save(path)
    item = fixture(1, "测试联赛")
    item.result_play.away = None
    result = run_daily_prediction([item], model_path=path, prefilter=EmptyPrefilter(), rule_budget=5, output_dir=tmp_path)
    assert result["matches"][0]["result"] is None
    assert result["matches"][0]["result_status"] == "待核实"


def test_prediction_rejects_fixture_not_after_training_cutoff(training_matches, tmp_path: Path):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    path = tmp_path / "m.json"; model.save(path)
    item = fixture(1, "测试联赛")
    item.kickoff_time = datetime.combine(model.training_cutoff, datetime.min.time(), timezone.utc)
    result = run_daily_prediction([item], model_path=path, prefilter=EmptyPrefilter(), rule_budget=5, output_dir=tmp_path)
    assert result["matches"][0]["model_status"] == "unavailable"
    assert "未来数据泄漏" in result["matches"][0]["reason"]


def test_neutral_competition_disables_home_advantage(training_matches, tmp_path: Path):
    model = GoalModel.fit(training_matches, competition="测试联赛", model_type="poisson")
    path = tmp_path / "m.json"; model.save(path)
    result = run_daily_prediction(
        [fixture(1, "测试联赛")], model_paths={"测试联赛": path},
        neutral_competitions={"测试联赛"}, prefilter=EmptyPrefilter(),
        rule_budget=5, output_dir=tmp_path,
    )
    assert result["matches"][0]["neutral"] is True
    expected = model.predict("A", "B", neutral=True)
    expected_result, _ = aggregate_difference_probabilities(
        {int(key): value for key, value in expected["difference_probabilities"].items()}, -1
    )
    assert result["matches"][0]["raw_probabilities"]["result"] == pytest.approx(expected_result)


def test_pending_or_cancelled_result_is_not_zero_or_denominator():
    prediction = {"matches": [{"match_id": "x", "result": "胜", "handicap_result": "让胜", "official_handicap": -1}]}
    pending = settle_review(prediction, [ResultInput(match_id="x", status="取消")])
    assert pending["denominators"] == {"result": 0, "handicap_result": 0}
    assert pending["settled"] == []


def test_extra_time_not_accepted_only_90_minute_fields_used():
    prediction = {"matches": [{"match_id": "x", "result": "平", "handicap_result": "让平", "official_handicap": 0}]}
    review = settle_review(
        prediction,
        [ResultInput(match_id="x", status="已完赛", home_goals_90=1, away_goals_90=1, verified_90_minutes=True)],
    )
    assert review["hits"] == {"result": 1, "handicap_result": 1}
    assert review["rules_modified"] is False
