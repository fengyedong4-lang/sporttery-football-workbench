from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.model import TrainingMatch


@pytest.fixture
def training_matches() -> list[TrainingMatch]:
    teams = ["A", "B", "C", "D"]
    rows: list[TrainingMatch] = []
    start = date(2025, 1, 1)
    for index in range(32):
        home = teams[index % 4]
        away = teams[(index + 1 + (index // 4) % 2) % 4]
        if home == away:
            away = teams[(teams.index(home) + 1) % 4]
        rows.append(
            TrainingMatch(
                match_id=f"m{index}",
                competition="测试联赛",
                kickoff_date=start + timedelta(days=index),
                home_team=home,
                away_team=away,
                home_goals=(index * 3) % 4,
                away_goals=(index * 5 + 1) % 3,
                neutral=False,
                source="isolated-test",
            )
        )
    return rows

