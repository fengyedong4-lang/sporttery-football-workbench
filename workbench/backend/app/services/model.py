from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

from .history import atomic_json_write


class ModelError(RuntimeError):
    pass


class UnknownTeamError(ModelError):
    pass


@dataclass(frozen=True)
class TrainingMatch:
    match_id: str
    competition: str
    kickoff_date: date
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    neutral: bool
    source: str


def import_training_csv(path: Path) -> list[TrainingMatch]:
    required = {
        "match_id", "competition", "kickoff_date", "home_team", "away_team",
        "home_goals_90", "away_goals_90", "neutral", "status", "source",
    }
    rows: list[TrainingMatch] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV缺少字段: {sorted(missing)}")
        for row in reader:
            if row["status"] != "completed_90":
                continue
            if row["match_id"] in seen:
                continue
            seen.add(row["match_id"])
            rows.append(
                TrainingMatch(
                    match_id=row["match_id"],
                    competition=row["competition"],
                    kickoff_date=date.fromisoformat(row["kickoff_date"]),
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    home_goals=int(row["home_goals_90"]),
                    away_goals=int(row["away_goals_90"]),
                    neutral=row["neutral"].strip().lower() in {"1", "true", "yes"},
                    source=row["source"],
                )
            )
    return rows


def import_football_data_csv(path: Path, *, competition: str, source_url: str) -> list[TrainingMatch]:
    """Import the common football-data CSV columns without performing a network request."""
    rows: list[TrainingMatch] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"football-data CSV缺少字段: {sorted(missing)}")
        for row in reader:
            parsed_date = None
            for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
                try:
                    parsed_date = datetime.strptime(row["Date"], fmt).date()
                    break
                except ValueError:
                    pass
            if parsed_date is None or row["FTR"] not in {"H", "D", "A"}:
                continue
            match_id = hashlib.sha256(
                f"{competition}|{parsed_date}|{row['HomeTeam']}|{row['AwayTeam']}".encode("utf-8")
            ).hexdigest()[:24]
            if match_id in seen:
                continue
            seen.add(match_id)
            rows.append(
                TrainingMatch(
                    match_id=match_id,
                    competition=competition,
                    kickoff_date=parsed_date,
                    home_team=row["HomeTeam"],
                    away_team=row["AwayTeam"],
                    home_goals=int(row["FTHG"]),
                    away_goals=int(row["FTAG"]),
                    neutral=False,
                    source=source_url,
                )
            )
    return rows


def export_training_csv(matches: list[TrainingMatch], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError("规范化训练集已存在，拒绝覆盖")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "match_id", "competition", "kickoff_date", "home_team", "away_team",
            "home_goals_90", "away_goals_90", "neutral", "status", "source",
        ])
        for match in matches:
            writer.writerow([
                match.match_id, match.competition, match.kickoff_date.isoformat(), match.home_team,
                match.away_team, match.home_goals, match.away_goals, str(match.neutral).lower(),
                "completed_90", match.source,
            ])


def _tau(home_goals: int, away_goals: int, home_rate: float, away_rate: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1 - home_rate * away_rate * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + home_rate * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + away_rate * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


class GoalModel:
    schema_version = 2

    def __init__(
        self,
        *,
        model_type: Literal["poisson", "dixon_coles"],
        competition: str,
        teams: list[str],
        attack: dict[str, float],
        defense: dict[str, float],
        baseline_log_rate: float,
        home_advantage: float,
        rho: float,
        decay: float,
        training_cutoff: date,
        training_data_sha256: str,
        created_at: str,
        validation: dict[str, Any],
        artifact_schema_version: int = 2,
    ) -> None:
        self.model_type = model_type
        self.competition = competition
        self.teams = teams
        self.attack = attack
        self.defense = defense
        self.baseline_log_rate = baseline_log_rate
        self.home_advantage = home_advantage
        self.rho = rho
        self.decay = decay
        self.training_cutoff = training_cutoff
        self.training_data_sha256 = training_data_sha256
        self.created_at = created_at
        self.validation = validation
        self.artifact_schema_version = artifact_schema_version

    @classmethod
    def fit(
        cls,
        matches: list[TrainingMatch],
        *,
        competition: str,
        model_type: Literal["poisson", "dixon_coles"],
        decay: float = 0.003,
        min_matches: int = 8,
    ) -> "GoalModel":
        sample = sorted((m for m in matches if m.competition == competition), key=lambda m: m.kickoff_date)
        if len(sample) < min_matches:
            raise ModelError(f"样本不足: {len(sample)} < {min_matches}")
        teams = sorted({m.home_team for m in sample} | {m.away_team for m in sample})
        if len(teams) < 3:
            raise ModelError("球队数不足")
        team_index = {team: index for index, team in enumerate(teams)}
        cutoff = sample[-1].kickoff_date
        n = len(teams)

        def unpack(parameters: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float, float]:
            attack = np.r_[parameters[: n - 1], -parameters[: n - 1].sum()]
            defense = np.r_[parameters[n - 1 : 2 * n - 2], -parameters[n - 1 : 2 * n - 2].sum()]
            baseline = parameters[-3]
            home = parameters[-2]
            rho = parameters[-1] if model_type == "dixon_coles" else 0.0
            return attack, defense, baseline, home, rho

        def objective(parameters: np.ndarray) -> float:
            attack, defense, baseline, home, rho = unpack(parameters)
            loss = 0.0
            for match in sample:
                weight = math.exp(-decay * (cutoff - match.kickoff_date).days)
                home_rate = math.exp(
                    baseline
                    + attack[team_index[match.home_team]]
                    + defense[team_index[match.away_team]]
                    + (0.0 if match.neutral else home)
                )
                away_rate = math.exp(
                    baseline + attack[team_index[match.away_team]] + defense[team_index[match.home_team]]
                )
                correction = _tau(match.home_goals, match.away_goals, home_rate, away_rate, rho)
                if correction <= 0 or not np.isfinite(home_rate + away_rate):
                    return 1e12
                log_probability = (
                    poisson.logpmf(match.home_goals, home_rate)
                    + poisson.logpmf(match.away_goals, away_rate)
                    + math.log(correction)
                )
                loss -= weight * log_probability
            loss += 0.001 * float(np.square(parameters[: 2 * n - 2]).sum())
            return loss

        parameter_count = 2 * (n - 1) + 3
        initial = np.zeros(parameter_count, dtype=float)
        mean_goals = sum(m.home_goals + m.away_goals for m in sample) / (2 * len(sample))
        initial[-3] = math.log(max(mean_goals, 0.1))
        initial[-2] = 0.15
        bounds = [(-3.0, 3.0)] * (parameter_count - 3) + [(-2.0, 2.0), (-1.0, 1.0), (-0.2, 0.2)]
        optimized = minimize(objective, initial, method="L-BFGS-B", bounds=bounds)
        if not optimized.success or not np.isfinite(optimized.fun):
            raise ModelError(f"训练未收敛: {optimized.message}")
        attack_values, defense_values, baseline_log_rate, home_advantage, rho = unpack(optimized.x)
        source_payload = [
            {
                **asdict(match),
                "kickoff_date": match.kickoff_date.isoformat(),
            }
            for match in sample
        ]
        fingerprint = hashlib.sha256(
            json.dumps(source_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return cls(
            model_type=model_type,
            competition=competition,
            teams=teams,
            attack={team: float(attack_values[index]) for team, index in team_index.items()},
            defense={team: float(defense_values[index]) for team, index in team_index.items()},
            baseline_log_rate=float(baseline_log_rate),
            home_advantage=float(home_advantage),
            rho=float(rho),
            decay=decay,
            training_cutoff=cutoff,
            training_data_sha256=fingerprint,
            created_at=datetime.now(timezone.utc).isoformat(),
            validation={
                "converged": True,
                "optimizer": "L-BFGS-B",
                "objective": float(optimized.fun),
                "matches": len(sample),
                "teams": len(teams),
                "probability_calibrated": False,
            },
            artifact_schema_version=cls.schema_version,
        )

    def _rates(self, home_team: str, away_team: str, neutral: bool) -> tuple[float, float]:
        missing = [team for team in (home_team, away_team) if team not in self.attack]
        if missing:
            raise UnknownTeamError(f"未知球队: {', '.join(missing)}")
        home_rate = math.exp(
            self.baseline_log_rate + self.attack[home_team] + self.defense[away_team] + (0.0 if neutral else self.home_advantage)
        )
        away_rate = math.exp(self.baseline_log_rate + self.attack[away_team] + self.defense[home_team])
        if not all(np.isfinite([home_rate, away_rate])) or min(home_rate, away_rate) <= 0:
            raise ModelError("进球率数值异常")
        return home_rate, away_rate

    def predict(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral: bool = False,
        tail_tolerance: float = 1e-8,
    ) -> dict[str, Any]:
        home_rate, away_rate = self._rates(home_team, away_team, neutral)
        maximum = 6
        while maximum < 40 and (
            poisson.sf(maximum, home_rate) + poisson.sf(maximum, away_rate) > tail_tolerance
        ):
            maximum += 1
        if maximum >= 40:
            raise ModelError("比分矩阵尾部无法在上限内收敛")
        goals = np.arange(maximum + 1)
        home_probs = poisson.pmf(goals, home_rate)
        away_probs = poisson.pmf(goals, away_rate)
        base_matrix = np.outer(home_probs, away_probs)
        tail_error = max(0.0, 1.0 - float(base_matrix.sum()))
        matrix = base_matrix.copy()
        if self.model_type == "dixon_coles":
            for home_goals, away_goals in ((0, 0), (0, 1), (1, 0), (1, 1)):
                matrix[home_goals, away_goals] *= _tau(
                    home_goals, away_goals, home_rate, away_rate, self.rho
                )
        if np.any(matrix < 0) or not np.isfinite(matrix).all():
            raise ModelError("比分矩阵出现负值或非有限值")
        matrix /= matrix.sum()
        differences: dict[int, float] = {}
        for home_goals in range(maximum + 1):
            for away_goals in range(maximum + 1):
                difference = home_goals - away_goals
                differences[difference] = differences.get(difference, 0.0) + float(
                    matrix[home_goals, away_goals]
                )
        probability_sum = sum(differences.values())
        if abs(probability_sum - 1.0) > 1e-10 or tail_error > tail_tolerance:
            raise ModelError(f"概率校验失败: sum={probability_sum}, tail={tail_error}")
        return {
            "home_rate": home_rate,
            "away_rate": away_rate,
            "max_goals": maximum,
            "tail_error": tail_error,
            "difference_probabilities": {str(key): value for key, value in sorted(differences.items())},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.artifact_schema_version,
            "model_type": self.model_type,
            "competition": self.competition,
            "teams": self.teams,
            "attack": self.attack,
            "defense": self.defense,
            "baseline_log_rate": self.baseline_log_rate,
            "home_advantage": self.home_advantage,
            "rho": self.rho,
            "decay": self.decay,
            "training_cutoff": self.training_cutoff.isoformat(),
            "training_data_sha256": self.training_data_sha256,
            "created_at": self.created_at,
            "validation": self.validation,
        }

    def save(self, path: Path) -> None:
        atomic_json_write(path, self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "GoalModel":
        data = json.loads(path.read_text(encoding="utf-8"))
        expected = {"schema_version", "model_type", "competition", "teams", "attack", "defense"}
        if data.get("schema_version") not in {1, 2} or not expected.issubset(data):
            raise ModelError("非本项目可信JSON模型格式")
        return cls(
            model_type=data["model_type"],
            competition=data["competition"],
            teams=list(data["teams"]),
            attack={key: float(value) for key, value in data["attack"].items()},
            defense={key: float(value) for key, value in data["defense"].items()},
            baseline_log_rate=float(data.get("baseline_log_rate", 0.0)),
            home_advantage=float(data["home_advantage"]),
            rho=float(data["rho"]),
            decay=float(data["decay"]),
            training_cutoff=date.fromisoformat(data["training_cutoff"]),
            training_data_sha256=data["training_data_sha256"],
            created_at=data["created_at"],
            validation=dict(data["validation"]),
            artifact_schema_version=int(data["schema_version"]),
        )
