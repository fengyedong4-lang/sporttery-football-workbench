from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any
import json

from .history import atomic_json_write
from .model import GoalModel, ModelError, TrainingMatch, UnknownTeamError
from .play_logic import aggregate_difference_probabilities, unique_pick


def walk_forward_backtest(
    matches: list[TrainingMatch],
    *,
    competition: str,
    model_type: str,
    min_train: int = 12,
    refit_every: int = 5,
) -> dict[str, Any]:
    ordered = sorted((m for m in matches if m.competition == competition), key=lambda m: m.kickoff_date)
    predictions: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    model: GoalModel | None = None
    last_fit_size = 0
    for current_date in sorted({match.kickoff_date for match in ordered}):
        day_matches = [match for match in ordered if match.kickoff_date == current_date]
        training = [match for match in ordered if match.kickoff_date < current_date]
        if len(training) < min_train:
            excluded.extend(
                {"match_id": match.match_id, "reason": "训练窗口不足"}
                for match in day_matches
            )
            continue
        if model is None or len(training) - last_fit_size >= refit_every:
            try:
                model = GoalModel.fit(
                    training,
                    competition=competition,
                    model_type=model_type,  # type: ignore[arg-type]
                    min_matches=min_train,
                )
                last_fit_size = len(training)
            except ModelError as exc:
                excluded.extend(
                    {"match_id": match.match_id, "reason": str(exc)} for match in day_matches
                )
                continue
        for match in day_matches:
            try:
                raw = model.predict(match.home_team, match.away_team, neutral=match.neutral)
            except ModelError as exc:
                excluded.append({"match_id": match.match_id, "reason": str(exc)})
                continue
            differences = {int(key): value for key, value in raw["difference_probabilities"].items()}
            probs, _ = aggregate_difference_probabilities(differences, None)
            actual = "胜" if match.home_goals > match.away_goals else "平" if match.home_goals == match.away_goals else "负"
            mapping = {"胜": "home", "平": "draw", "负": "away"}
            p_actual = max(probs[actual], 1e-15)
            one_hot = {key: 1.0 if key == actual else 0.0 for key in probs}
            predictions.append(
                {
                    "match_id": match.match_id,
                    "date": match.kickoff_date.isoformat(),
                    "training_cutoff": model.training_cutoff.isoformat(),
                    "pick": unique_pick(probs),
                    "actual": actual,
                    "hit": unique_pick(probs) == actual,
                    "brier": sum((probs[key] - one_hot[key]) ** 2 for key in probs) / 3,
                    "log_loss": -__import__("math").log(p_actual),
                    "probabilities": {mapping[key]: value for key, value in probs.items()},
                }
            )
    denominator = len(predictions)
    return {
        "mode": "reconstructed_walk_forward_backtest",
        "competition": competition,
        "model_type": model_type,
        "denominator": denominator,
        "hits": sum(item["hit"] for item in predictions),
        "coverage": denominator / len(ordered) if ordered else 0.0,
        "brier": sum(item["brier"] for item in predictions) / denominator if denominator else None,
        "log_loss": sum(item["log_loss"] for item in predictions) / denominator if denominator else None,
        "handicap_evaluation": "资料不足；未提供历史官方让球快照",
        "predictions": predictions,
        "excluded": excluded,
        "no_future_leakage": all(item["training_cutoff"] < item["date"] for item in predictions),
    }


def save_backtest(report: dict[str, Any], path: Path) -> None:
    atomic_json_write(path, report)


def compare_backtests(baseline_path: Path, candidate_path: Path) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    baseline_rows = {row["match_id"]: row for row in baseline.get("predictions", [])}
    candidate_rows = {row["match_id"]: row for row in candidate.get("predictions", [])}
    common = sorted(set(baseline_rows) & set(candidate_rows))

    def summary(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
        selected = [rows[match_id] for match_id in common]
        count = len(selected)
        return {
            "model_type": baseline.get("model_type") if rows is baseline_rows else candidate.get("model_type"),
            "denominator": count,
            "hits": sum(row["hit"] for row in selected),
            "brier": sum(row["brier"] for row in selected) / count if count else None,
            "log_loss": sum(row["log_loss"] for row in selected) / count if count else None,
        }

    return {
        "mode": "paired_model_comparison",
        "competition": baseline.get("competition"),
        "common_sample_count": len(common),
        "common_match_ids": common,
        "baseline": summary(baseline_rows),
        "candidate": summary(candidate_rows),
        "same_valid_sample": True,
    }
