#!/usr/bin/env python3
"""Fetch compact pre-match evidence for the 2026-09-20 Sporttery slate."""

from __future__ import annotations

import concurrent.futures
import json
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


MATCH_IDS = {
    "周日001": 6052516,
    "周日002": 5803592,
    "周日003": 5803590,
    "周日004": 5140040,
    "周日005": 6052478,
    "周日006": 5749683,
    "周日007": 5836848,
    "周日008": 5868076,
    "周日009": 5781753,
    "周日010": 5795460,
    "周日011": 5795455,
    "周日012": 5795461,
    "周日013": 5749687,
    "周日014": 5749684,
    "周日015": 5881170,
    "周日016": 5868072,
    "周日017": 5107607,
    "周日018": 5105015,
    "周日019": 5802942,
    "周日020": 5795459,
    "周日021": 5881176,
    "周日022": 5749685,
    "周日023": 5868080,
    "周日024": 5868074,
    "周日025": 5881175,
    "周日026": 5749680,
    "周日027": 5802945,
    "周日028": 5868079,
    "周日029": 5887641,
    "周日030": 5103641,
}


def fetch(match_number: str, match_id: int) -> tuple[str, dict]:
    url = f"https://www.fotmob.com/api/data/matchDetails?matchId={match_id}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.fotmob.com/"},
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return match_number, json.loads(response.read())
        except Exception as exc:  # transient TLS throttling is common on parallel fetches
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{match_number} FotMob fetch failed after retries: {last_error}")


def form_summary(items: list[dict], team_id: int) -> dict:
    finished = [item for item in items if item.get("score") and item.get("resultString") in {"W", "D", "L"}][-5:]
    points = wins = draws = losses = goals_for = goals_against = 0
    rows = []
    for item in finished:
        result = item["resultString"]
        wins += result == "W"
        draws += result == "D"
        losses += result == "L"
        points += 3 if result == "W" else 1 if result == "D" else 0
        tip = item.get("tooltipText") or {}
        home_id = int(tip.get("homeTeamId") or 0)
        hs = int(tip.get("homeScore") or 0)
        aw = int(tip.get("awayScore") or 0)
        if home_id == team_id:
            gf, ga = hs, aw
        else:
            gf, ga = aw, hs
        goals_for += gf
        goals_against += ga
        rows.append(
            {
                "date": (item.get("date") or {}).get("utcTime"),
                "result": result,
                "score": item.get("score"),
                "home": tip.get("homeTeam"),
                "away": tip.get("awayTeam"),
            }
        )
    return {
        "sample": len(finished),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "points": points,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "matches": rows,
    }


def lineup_summary(lineup: dict, side: str) -> dict:
    team = lineup.get(side) or {}
    starters = team.get("starters") or []
    return {
        "lineup_type": lineup.get("lineupType"),
        "formation": team.get("formation"),
        "starter_count": len(starters),
        "market_value_sum": sum(int(player.get("marketValue") or 0) for player in starters),
        "starter_names": [player.get("name") for player in starters],
    }


def h2h_summary(h2h: dict, home_id: int, away_id: int) -> dict:
    cutoff = datetime(2023, 9, 20, tzinfo=timezone.utc)
    home_wins = draws = away_wins = 0
    rows = []
    for item in h2h.get("matches") or []:
        status = item.get("status") or {}
        when = ((item.get("time") or {}).get("utcTime"))
        if not status.get("finished") or not when:
            continue
        parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if parsed < cutoff:
            continue
        score = status.get("scoreStr") or ""
        try:
            hs, aw = [int(part.strip()) for part in score.split("-")]
        except (ValueError, AttributeError):
            continue
        item_home = int((item.get("home") or {}).get("id") or 0)
        item_away = int((item.get("away") or {}).get("id") or 0)
        if item_home == home_id and item_away == away_id:
            home_goals, away_goals = hs, aw
        elif item_home == away_id and item_away == home_id:
            home_goals, away_goals = aw, hs
        else:
            continue
        home_wins += home_goals > away_goals
        draws += home_goals == away_goals
        away_wins += home_goals < away_goals
        rows.append({"date": when, "score_from_current_home_view": f"{home_goals}-{away_goals}"})
    return {
        "sample": len(rows),
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "matches": rows[:8],
    }


def compact(match_number: str, raw: dict) -> dict:
    general = raw.get("general") or {}
    facts = ((raw.get("content") or {}).get("matchFacts") or {})
    forms = facts.get("teamForm") or [[], []]
    lineup = ((raw.get("content") or {}).get("lineup") or {})
    home_id = int((general.get("homeTeam") or {}).get("id") or 0)
    away_id = int((general.get("awayTeam") or {}).get("id") or 0)
    return {
        "match_number": match_number,
        "fotmob_match_id": int(general.get("matchId") or 0),
        "source_url": f"https://www.fotmob.com/api/data/matchDetails?matchId={general.get('matchId')}",
        "league": general.get("leagueName"),
        "round": general.get("matchRound"),
        "home_team": (general.get("homeTeam") or {}).get("name"),
        "away_team": (general.get("awayTeam") or {}).get("name"),
        "home_form": form_summary(forms[0] if len(forms) > 0 else [], home_id),
        "away_form": form_summary(forms[1] if len(forms) > 1 else [], away_id),
        "home_lineup": lineup_summary(lineup, "homeTeam"),
        "away_lineup": lineup_summary(lineup, "awayTeam"),
        "h2h_three_years": h2h_summary(((raw.get("content") or {}).get("h2h") or {}), home_id, away_id),
        "weather": (raw.get("content") or {}).get("weather") or {},
        "lineup_status": "predicted" if lineup.get("lineupType") == "predicted" else "unavailable",
        "evidence_limit": "FotMob third-party pre-match data; predicted lineup is not a confirmed starting XI.",
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, number, match_id) for number, match_id in MATCH_IDS.items()]
        raw = dict(future.result() for future in concurrent.futures.as_completed(futures))
    output = {
        "source": "FotMob public pre-match API",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "matches": [compact(number, raw[number]) for number in MATCH_IDS],
    }
    target = Path(tempfile.gettempdir()) / "fotmob_research_2026-09-20.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    for item in output["matches"]:
        hf, af, h2h = item["home_form"], item["away_form"], item["h2h_three_years"]
        print(
            f"{item['match_number']} {item['home_team']} vs {item['away_team']} | "
            f"form {hf['wins']}-{hf['draws']}-{hf['losses']} {hf['goals_for']}:{hf['goals_against']} / "
            f"{af['wins']}-{af['draws']}-{af['losses']} {af['goals_for']}:{af['goals_against']} | "
            f"h2h {h2h['home_wins']}-{h2h['draws']}-{h2h['away_wins']} | "
            f"XI value {item['home_lineup']['market_value_sum']}/{item['away_lineup']['market_value_sum']}"
        )


if __name__ == "__main__":
    main()
