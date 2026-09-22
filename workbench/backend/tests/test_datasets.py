import json

import pytest

from app.services.datasets import (
    DatasetSpec,
    _parse_fixture_download,
    _parse_jleague,
    _parse_openfootball_txt,
    _parse_openfootball_worldcup,
)


def spec(adapter: str, competition: str = "测试杯赛") -> DatasetSpec:
    return DatasetSpec("test", competition, "2025/26", "https://raw.githubusercontent.com/a/b/c", adapter, 1, "test")


def test_world_cup_uses_ft_not_extra_time():
    body = json.dumps({"matches": [{"date": "2026-07-19", "team1": "A", "team2": "B", "score": {"ft": [0, 0], "et": [1, 0]}}]}).encode()
    matches, raw_count, excluded = _parse_openfootball_worldcup(spec("openfootball_worldcup"), body)
    assert raw_count == 1
    assert excluded == []
    assert (matches[0].home_goals, matches[0].away_goals) == (0, 0)
    assert matches[0].neutral is True


def test_ucl_keeps_raw_count_but_excludes_ambiguous_extra_time():
    body = json.dumps([
        {"MatchNumber": 1, "DateUtc": "2025-09-16 19:00:00Z", "HomeTeam": "A", "AwayTeam": "B", "HomeTeamScore": 1, "AwayTeamScore": 0},
        {"MatchNumber": 159, "DateUtc": "2026-02-25 20:00:00Z", "HomeTeam": "Juventus", "AwayTeam": "Galatasaray", "HomeTeamScore": 3, "AwayTeamScore": 2},
    ]).encode()
    matches, raw_count, excluded = _parse_fixture_download(spec("fixture_download", "欧冠"), body)
    assert raw_count == 2
    assert len(matches) == 1
    assert len(excluded) == 1
    assert "加时或点球" in excluded[0]


def test_jleague_penalty_status_keeps_90_minute_draw():
    body = (",match_date,home_team,home_goal,away_goal,away_team,status\n"
            "0,2026/02/06,京都,1,1,神戸,試合終了(1 PK 4)\n").encode()
    matches, raw_count, excluded = _parse_jleague(spec("jleague", "日职"), body)
    assert raw_count == 1 and excluded == []
    assert (matches[0].home_goals, matches[0].away_goals) == (1, 1)


def test_openfootball_txt_excludes_extra_time_but_counts_raw():
    body = ("  Tue Jul 8 2025\n    A (ENG) v B (GER) 1-0 (0-0)\n"
            "  Wed Jul 9\n    C (FRA) v D (ITA) 2-1 a.e.t. (1-1)\n").encode()
    matches, raw_count, excluded = _parse_openfootball_txt(spec("openfootball_txt"), body)
    assert raw_count == 2 and len(matches) == 1 and len(excluded) == 1
