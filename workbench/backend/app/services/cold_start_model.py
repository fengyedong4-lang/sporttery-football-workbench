from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from numbers import Integral
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import poisson

from .model import ModelError


REGULARIZATION_STRENGTH = 4.0
_MIN_TEAM_FIT_MATCHES = 4
_RESULT_LABELS = ("胜", "平", "负")
_CHINA = timezone(timedelta(hours=8), "Asia/Shanghai")


@dataclass(frozen=True)
class _Match:
    match_id: str
    match_date: date
    home_team_id: str
    away_team_id: str
    home_goals_90: int
    away_goals_90: int
    competition_id: str
    season_id: str
    source_ids: tuple[str, ...]

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "match_date": self.match_date.isoformat(),
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "home_goals_90": self.home_goals_90,
            "away_goals_90": self.away_goals_90,
            "competition_id": self.competition_id,
            "season_id": self.season_id,
            "status": "completed_90",
            "score_basis": "90_minutes",
            "source_ids": list(self.source_ids),
            "is_friendly": False,
        }


def _nonempty_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _parse_goals(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, Integral):
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


def _fact_key(match: _Match) -> tuple[Any, ...]:
    return (
        match.match_date,
        match.home_team_id,
        match.away_team_id,
        match.home_goals_90,
        match.away_goals_90,
        match.competition_id,
        match.season_id,
    )


def _prepare_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    competition_id: str,
    season_id: str,
    cutoff: datetime | None,
    include_seasons: bool = False,
    isolate_generations: bool = False,
) -> tuple[list[_Match], dict[str, int]]:
    excluded: Counter[str] = Counter()
    grouped: dict[str, list[_Match]] = defaultdict(list)
    cutoff_date = cutoff.astimezone(_CHINA).date() if cutoff is not None else None

    for row in rows:
        if not isinstance(row, Mapping):
            excluded["not_mapping"] += 1
            continue
        if row.get("competition_id") != competition_id:
            excluded["other_competition"] += 1
            continue
        if row.get("season_id") != season_id and not include_seasons:
            excluded["other_season"] += 1
            continue
        if row.get("status") != "completed_90" or row.get("score_basis") != "90_minutes":
            excluded["not_completed_90"] += 1
            continue
        if row.get("is_friendly") is not False:
            excluded["friendly_or_unknown_type"] += 1
            continue

        match_id = _nonempty_id(row.get("match_id"))
        home_team_id = _nonempty_id(row.get("home_team_id"))
        away_team_id = _nonempty_id(row.get("away_team_id"))
        source_id = _nonempty_id(row.get("source_id"))
        match_date = _parse_date(row.get("match_date"))
        if (
            match_id is None
            or home_team_id is None
            or away_team_id is None
            or source_id is None
            or match_date is None
            or home_team_id == away_team_id
        ):
            excluded["invalid_identity"] += 1
            continue
        if cutoff_date is not None and match_date >= cutoff_date:
            excluded["on_or_after_cutoff_date"] += 1
            continue
        if include_seasons and match_date < date(2025, 1, 1):
            excluded["before_2025_window"] += 1
            continue
        if isolate_generations and row.get("season_id") != season_id:
            # Previous youth editions may inform only the tournament scoring
            # environment; their learned team parameters cannot hit today's ID.
            home_team_id = f"edition:{row.get('season_id')}:{home_team_id}"
            away_team_id = f"edition:{row.get('season_id')}:{away_team_id}"

        home_goals = _parse_goals(row.get("home_goals_90"))
        away_goals = _parse_goals(row.get("away_goals_90"))
        if home_goals is None or away_goals is None:
            excluded["invalid_score"] += 1
            continue
        grouped[match_id].append(
            _Match(
                match_id=match_id,
                match_date=match_date,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                home_goals_90=home_goals,
                away_goals_90=away_goals,
                competition_id=competition_id,
                season_id=str(row.get("season_id")),
                source_ids=(source_id,),
            )
        )

    accepted: list[_Match] = []
    for match_id, versions in grouped.items():
        facts = {_fact_key(version) for version in versions}
        if len(facts) != 1:
            excluded["conflicting_duplicate_rows"] += len(versions)
            continue
        first = versions[0]
        sources = tuple(sorted({source for version in versions for source in version.source_ids}))
        accepted.append(
            _Match(
                match_id=first.match_id,
                match_date=first.match_date,
                home_team_id=first.home_team_id,
                away_team_id=first.away_team_id,
                home_goals_90=first.home_goals_90,
                away_goals_90=first.away_goals_90,
                competition_id=first.competition_id,
                season_id=first.season_id,
                source_ids=sources,
            )
        )
        if len(versions) > 1:
            excluded["identical_duplicate_rows"] += len(versions) - 1

    accepted.sort(key=lambda match: (match.match_date, match.match_id))
    return accepted, dict(sorted(excluded.items()))


def _fingerprint(matches: Sequence[_Match]) -> str:
    payload = [match.fingerprint_payload() for match in matches]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _difference_probabilities(
    home_rate: float,
    away_rate: float,
    *,
    tail_tolerance: float = 1e-10,
) -> tuple[dict[int, float], float, int]:
    maximum = 6
    while maximum < 60 and (
        float(poisson.sf(maximum, home_rate)) + float(poisson.sf(maximum, away_rate))
        > tail_tolerance
    ):
        maximum += 1
    if maximum >= 60:
        raise ModelError("比分概率尾部无法在安全上限内收敛")

    goals = np.arange(maximum + 1)
    matrix = np.outer(poisson.pmf(goals, home_rate), poisson.pmf(goals, away_rate))
    mass = float(matrix.sum())
    tail_error = max(0.0, 1.0 - mass)
    if mass <= 0 or not np.isfinite(matrix).all():
        raise ModelError("比分概率矩阵数值异常")
    matrix /= mass

    differences: dict[int, float] = {}
    for home_goals in range(maximum + 1):
        for away_goals in range(maximum + 1):
            difference = home_goals - away_goals
            differences[difference] = differences.get(difference, 0.0) + float(
                matrix[home_goals, away_goals]
            )
    return dict(sorted(differences.items())), tail_error, maximum


def _result_probabilities(differences: Mapping[int, float]) -> dict[str, float]:
    result = {label: 0.0 for label in _RESULT_LABELS}
    for difference, probability in differences.items():
        result["胜" if difference > 0 else "平" if difference == 0 else "负"] += probability
    return result


class ColdStartModel:
    """Competition-season-local Poisson model for sparse, verified 90-minute results."""

    schema_version = 1

    def __init__(
        self,
        *,
        competition_id: str,
        season_id: str,
        cutoff: datetime,
        baseline_rate: float,
        teams: list[str],
        attack: dict[str, float],
        defense: dict[str, float],
        team_match_counts: dict[str, int],
        training_match_count: int,
        valid_rows_sha256: str,
        excluded_rows: dict[str, int],
        fit_status: str,
        optimizer: dict[str, Any],
    ) -> None:
        self.competition_id = competition_id
        self.season_id = season_id
        self.cutoff = cutoff
        self.baseline_rate = baseline_rate
        self.teams = teams
        self.attack = attack
        self.defense = defense
        self.team_match_counts = team_match_counts
        self.training_match_count = training_match_count
        self.valid_rows_sha256 = valid_rows_sha256
        self.excluded_rows = excluded_rows
        self.fit_status = fit_status
        self.optimizer = optimizer

    @classmethod
    def fit(
        cls,
        rows: list[dict[str, Any]],
        *,
        competition_id: str,
        season_id: str,
        cutoff: datetime,
        include_seasons: bool = False,
        isolate_generations: bool = False,
    ) -> "ColdStartModel":
        if not isinstance(cutoff, datetime):
            raise TypeError("cutoff必须是datetime")
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ModelError("cutoff必须包含时区；官方match_date按Asia/Shanghai日期判断")
        competition = _nonempty_id(competition_id)
        season = _nonempty_id(season_id)
        if competition is None or season is None:
            raise ModelError("competition_id和season_id不能为空")
        matches, excluded = _prepare_rows(
            rows,
            competition_id=competition,
            season_id=season,
            cutoff=cutoff,
            include_seasons=include_seasons,
            isolate_generations=isolate_generations,
        )
        return cls._fit_prepared(
            matches,
            competition_id=competition,
            season_id=season,
            cutoff=cutoff,
            excluded_rows=excluded,
        )

    @classmethod
    def _fit_prepared(
        cls,
        matches: Sequence[_Match],
        *,
        competition_id: str,
        season_id: str,
        cutoff: datetime,
        excluded_rows: dict[str, int] | None = None,
    ) -> "ColdStartModel":
        if not matches:
            raise ModelError("没有符合赛事、赛季、90分钟口径和截止时间的有效训练样本")

        teams = sorted(
            {match.home_team_id for match in matches} | {match.away_team_id for match in matches}
        )
        counts: Counter[str] = Counter()
        for match in matches:
            counts[match.home_team_id] += 1
            counts[match.away_team_id] += 1

        total_goals = sum(match.home_goals_90 + match.away_goals_90 for match in matches)
        # Jeffreys-style half-goal smoothing keeps an all-zero sparse sample numerically usable.
        baseline_rate = (total_goals + 0.5) / (2.0 * len(matches))
        baseline_log_rate = math.log(baseline_rate)
        attack = {team: 0.0 for team in teams}
        defense = {team: 0.0 for team in teams}
        fit_status = "baseline_only_prior_dominated"
        optimizer: dict[str, Any] = {
            "used": False,
            "method": None,
            "converged": None,
            "regularization_strength": REGULARIZATION_STRENGTH,
            "regularization_tuning": "fixed_engineering_hyperparameter_not_tuned",
        }

        if len(matches) >= _MIN_TEAM_FIT_MATCHES:
            team_index = {team: index for index, team in enumerate(teams)}
            home_indices = np.array([team_index[match.home_team_id] for match in matches])
            away_indices = np.array([team_index[match.away_team_id] for match in matches])
            home_goals = np.array([match.home_goals_90 for match in matches], dtype=float)
            away_goals = np.array([match.away_goals_90 for match in matches], dtype=float)
            constants = gammaln(home_goals + 1.0) + gammaln(away_goals + 1.0)
            team_count = len(teams)

            def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
                attack_values = parameters[:team_count]
                defense_values = parameters[team_count:]
                home_eta = (
                    baseline_log_rate
                    + attack_values[home_indices]
                    - defense_values[away_indices]
                )
                away_eta = (
                    baseline_log_rate
                    + attack_values[away_indices]
                    - defense_values[home_indices]
                )
                home_rates = np.exp(home_eta)
                away_rates = np.exp(away_eta)
                home_residuals = home_rates - home_goals
                away_residuals = away_rates - away_goals
                loss = float(
                    np.sum(
                        home_rates
                        - home_goals * home_eta
                        + away_rates
                        - away_goals * away_eta
                        + constants
                    )
                    + 0.5 * REGULARIZATION_STRENGTH * np.dot(parameters, parameters)
                )
                gradient = REGULARIZATION_STRENGTH * parameters.copy()
                np.add.at(gradient[:team_count], home_indices, home_residuals)
                np.add.at(gradient[:team_count], away_indices, away_residuals)
                np.add.at(gradient[team_count:], away_indices, -home_residuals)
                np.add.at(gradient[team_count:], home_indices, -away_residuals)
                return loss, gradient

            optimized = minimize(
                objective,
                np.zeros(2 * team_count, dtype=float),
                method="L-BFGS-B",
                jac=True,
                bounds=[(-3.0, 3.0)] * (2 * team_count),
                options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
            )
            if not optimized.success or not np.isfinite(optimized.fun):
                raise ModelError(f"冷启动MAP训练未收敛: {optimized.message}")
            for team, index in team_index.items():
                attack[team] = float(optimized.x[index])
                defense[team] = float(optimized.x[team_count + index])
            fit_status = "regularized_poisson_map"
            optimizer.update(
                {
                    "used": True,
                    "method": "L-BFGS-B",
                    "converged": True,
                    "objective": float(optimized.fun),
                    "iterations": int(optimized.nit),
                    "analytic_gradient": True,
                }
            )

        return cls(
            competition_id=competition_id,
            season_id=season_id,
            cutoff=cutoff,
            baseline_rate=float(baseline_rate),
            teams=teams,
            attack=attack,
            defense=defense,
            team_match_counts=dict(sorted(counts.items())),
            training_match_count=len(matches),
            valid_rows_sha256=_fingerprint(matches),
            excluded_rows=dict(excluded_rows or {}),
            fit_status=fit_status,
            optimizer=optimizer,
        )

    def predict(
        self,
        home_team_id: str,
        away_team_id: str,
        *,
        prior_only: bool = False,
        tail_tolerance: float = 1e-10,
    ) -> dict[str, Any]:
        home = _nonempty_id(home_team_id)
        away = _nonempty_id(away_team_id)
        if home is None or away is None or home == away:
            raise ModelError("预测必须提供两个不同的非空球队ID")
        if not (0 < tail_tolerance < 1):
            raise ValueError("tail_tolerance必须在0和1之间")

        if not isinstance(prior_only, bool):
            raise TypeError("prior_only必须是bool")
        home_count = self.team_match_counts.get(home, 0)
        away_count = self.team_match_counts.get(away, 0)
        if prior_only:
            home_rate = away_rate = self.baseline_rate
        else:
            home_rate = self.baseline_rate * math.exp(
                self.attack.get(home, 0.0) - self.defense.get(away, 0.0)
            )
            away_rate = self.baseline_rate * math.exp(
                self.attack.get(away, 0.0) - self.defense.get(home, 0.0)
            )
        if not all(math.isfinite(rate) and rate > 0 for rate in (home_rate, away_rate)):
            raise ModelError("预测进球率数值异常")

        differences, tail_error, maximum = _difference_probabilities(
            home_rate,
            away_rate,
            tail_tolerance=tail_tolerance,
        )
        prior_teams = [
            team
            for team, count in ((home, home_count), (away, away_count))
            if count < _MIN_TEAM_FIT_MATCHES
        ]
        return {
            "home_rate": float(home_rate),
            "away_rate": float(away_rate),
            "difference_probabilities": differences,
            "tail_error": tail_error,
            "max_goals": maximum,
            "sample_counts": {
                "training_matches": self.training_match_count,
                "home_team_matches": home_count,
                "away_team_matches": away_count,
            },
            "prior_dominated": self.fit_status == "baseline_only_prior_dominated"
            or bool(prior_teams)
            or prior_only,
            "prior_dominated_teams": [home, away] if prior_only else prior_teams,
            "unknown_teams": [team for team in (home, away) if team not in self.team_match_counts],
            "prior_only": prior_only,
            "probability_calibrated": False,
            "venue_modelled": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_type": "competition_season_regularized_poisson_map",
            "competition_id": self.competition_id,
            "season_id": self.season_id,
            "cutoff": self.cutoff.isoformat(),
            "training_match_count": self.training_match_count,
            "valid_rows_sha256": self.valid_rows_sha256,
            "excluded_rows": self.excluded_rows,
            "teams": self.teams,
            "team_match_counts": self.team_match_counts,
            "baseline_rate": self.baseline_rate,
            "prior_center_source": "same_competition_and_season_verified_90_minute_goals",
            "attack": self.attack,
            "defense": self.defense,
            "fit_status": self.fit_status,
            "optimizer": self.optimizer,
            "venue_modelled": False,
            "home_advantage_modelled": False,
            "cross_competition_transfer": False,
            "odds_used": False,
            "probability_calibrated": False,
        }


def temporal_evaluate(
    rows: list[dict[str, Any]],
    competition_id: str,
    season_id: str,
    min_train: int = 8,
    include_seasons: bool = False,
    isolate_generations: bool = False,
    max_folds: int | None = None,
) -> dict[str, Any]:
    if isinstance(min_train, bool) or not isinstance(min_train, int) or min_train < 1:
        raise ValueError("min_train必须是正整数")
    competition = _nonempty_id(competition_id)
    season = _nonempty_id(season_id)
    if competition is None or season is None:
        raise ModelError("competition_id和season_id不能为空")

    matches, excluded = _prepare_rows(
        rows,
        competition_id=competition,
        season_id=season,
        cutoff=None,
        include_seasons=include_seasons,
        isolate_generations=isolate_generations,
    )
    base_report: dict[str, Any] = {
        "competition_id": competition,
        "season_id": season,
        "min_train": min_train,
        "valid_match_count": len(matches),
        "valid_rows_sha256": _fingerprint(matches),
        "excluded_rows": excluded,
        "denominator": 0,
        "no_future_leakage": True,
        "same_day_training_excluded": True,
        "probability_calibrated": False,
        "folds": [],
        "source_availability_replayed": False,
        "evaluation_type": "retrospective_time_split_by_match_date_not_point_in_time_source_archive",
    }
    if not matches:
        return {**base_report, "status": "not_enough_data", "reason": "no_valid_rows"}

    by_date: dict[date, list[_Match]] = defaultdict(list)
    for match in matches:
        by_date[match.match_date].append(match)

    model_brier: list[float] = []
    model_log_loss: list[float] = []
    model_hits: list[bool] = []
    baseline_brier: list[float] = []
    baseline_log_loss: list[float] = []
    baseline_hits: list[bool] = []
    folds: list[dict[str, Any]] = []
    epsilon = 1e-15

    test_dates = sorted(by_date)
    if max_folds is not None and len(test_dates) > max_folds:
        test_dates = test_dates[-max_folds:]
        base_report["evaluation_window"] = f"last_{max_folds}_distinct_dates"
    for test_date in test_dates:
        training = [match for match in matches if match.match_date < test_date]
        if len(training) < min_train:
            continue
        cutoff = datetime.combine(test_date, time.min, tzinfo=_CHINA)
        model = ColdStartModel._fit_prepared(
            training,
            competition_id=competition,
            season_id=season,
            cutoff=cutoff,
        )
        baseline_differences, _, _ = _difference_probabilities(
            model.baseline_rate,
            model.baseline_rate,
        )
        baseline_probs = _result_probabilities(baseline_differences)
        folds.append(
            {
                "test_date": test_date.isoformat(),
                "test_matches": len(by_date[test_date]),
                "training_matches": len(training),
                "latest_training_date": max(match.match_date for match in training).isoformat(),
                "training_rows_sha256": model.valid_rows_sha256,
            }
        )

        for match in by_date[test_date]:
            prediction = model.predict(match.home_team_id, match.away_team_id)
            probabilities = _result_probabilities(prediction["difference_probabilities"])
            actual = (
                "胜"
                if match.home_goals_90 > match.away_goals_90
                else "平"
                if match.home_goals_90 == match.away_goals_90
                else "负"
            )
            target = {label: float(label == actual) for label in _RESULT_LABELS}
            model_brier.append(
                sum((probabilities[label] - target[label]) ** 2 for label in _RESULT_LABELS)
            )
            model_log_loss.append(-math.log(max(probabilities[actual], epsilon)))
            model_hits.append(max(_RESULT_LABELS, key=probabilities.__getitem__) == actual)
            baseline_brier.append(
                sum((baseline_probs[label] - target[label]) ** 2 for label in _RESULT_LABELS)
            )
            baseline_log_loss.append(-math.log(max(baseline_probs[actual], epsilon)))
            baseline_hits.append(max(_RESULT_LABELS, key=baseline_probs.__getitem__) == actual)

    if not model_hits:
        return {
            **base_report,
            "status": "not_enough_data",
            "reason": "no_date_has_required_strictly_earlier_training_prefix",
        }

    denominator = len(model_hits)
    return {
        **base_report,
        "status": "ok",
        "denominator": denominator,
        "brier": float(np.mean(model_brier)),
        "log_loss": float(np.mean(model_log_loss)),
        "accuracy": sum(model_hits) / denominator,
        "baseline": {
            "description": "same-prefix competition-season goal-rate only",
            "brier": float(np.mean(baseline_brier)),
            "log_loss": float(np.mean(baseline_log_loss)),
            "accuracy": sum(baseline_hits) / denominator,
            "denominator": denominator,
        },
        "folds": folds,
    }
