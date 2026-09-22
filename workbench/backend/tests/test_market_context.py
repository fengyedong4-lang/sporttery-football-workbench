from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.market_context import build_market_context


def setup():
    now = datetime.now(timezone.utc)
    fixture = SimpleNamespace(match_id="10", kickoff_time=now+timedelta(days=1), home_team="甲", away_team="乙", competition="联赛")
    research = {"sources": [{"source_id": "s", "sha256": "a"*64, "url": "https://example.com/source"}], "market": {"observations": []}, "facts": []}
    return now, fixture, research


def observation(when, **kwargs):
    return {"fixture_id": "10", "source_id": "s", "bookmaker": "Company", "market_type": "european_1x2", "observed_at": when.isoformat(), "home": 2.0, "draw": 3.0, "away": 4.0, **kwargs}


def test_single_snapshot_is_not_a_price_change():
    now, fixture, research = setup()
    research["market"]["observations"] = [observation(now-timedelta(minutes=5))]
    result = build_market_context(fixture, research)
    assert result["status"] == "snapshot_only" and not result["changes"]
    assert result["reference_budget"]["market"] == .2
    assert result["probabilities_modified"] is False


def test_only_same_bookmaker_market_line_is_comparable():
    now, fixture, research = setup()
    research["market"]["observations"] = [observation(now-timedelta(hours=1)), observation(now-timedelta(minutes=5),home=1.8), observation(now-timedelta(minutes=2),bookmaker="Other")]
    result = build_market_context(fixture, research)
    assert len(result["changes"]) == 1
    assert result["changes"][0]["implied_probability_change"]["home"] > 0


def test_future_unknown_sources_and_same_time_conflicts_are_rejected():
    now, fixture, research = setup()
    research["market"]["observations"] = [observation(now-timedelta(minutes=2)), observation(now-timedelta(minutes=2),home=1.8), observation(now+timedelta(hours=1)), observation(now-timedelta(minutes=1),source_id="unknown")]
    result = build_market_context(fixture, research)
    assert not result["snapshots"] and not result["changes"]
    assert result["reference_budget"]["market"] == 0


def test_upset_boost_needs_paired_pre_match_market_and_results():
    now, fixture, research = setup()
    research["market"]["observations"] = [observation(now-timedelta(minutes=5))]
    rows = [{"match_id": str(i), "source_id": "historical", "source_verified": True,
             "observed_at": (now-timedelta(days=i+2)).isoformat(), "kickoff_time": (now-timedelta(days=i+1)).isoformat(),
             "odds": {"home":1.5,"draw":4.,"away":7.}, "actual_result": "away" if i<3 else "home"} for i in range(8)]
    assert build_market_context(fixture,research,historical_rows=rows)["reference_budget"]["market"] == .3
    assert build_market_context(fixture,research,historical_rows=rows[:3])["reference_budget"]["market"] == .2
    rows[0]["observed_at"] = now.isoformat()
    assert build_market_context(fixture,research,historical_rows=rows)["upset_profile"]["frequent"] is False


def test_asian_line_move_is_reported_without_comparing_different_event_prices():
    now, fixture, research = setup()
    research["market"]["observations"] = [observation(now-timedelta(hours=1),market_type="asian_handicap",line=-.5,draw=None),
                                               observation(now-timedelta(minutes=5),market_type="asian_handicap",line=-.75,draw=None)]
    result = build_market_context(fixture,research)
    assert len(result["changes"]) == 1
    assert result["changes"][0]["change_type"] == "line_change"
    assert result["changes"][0]["implied_probability_change"] is None


def test_same_prices_at_two_times_are_not_reported_as_change():
    now, fixture, research = setup()
    research["market"]["observations"] = [observation(now-timedelta(hours=1)), observation(now-timedelta(minutes=5))]
    result = build_market_context(fixture,research)
    assert result["status"] == "unchanged_verified"
    assert not result["changes"] and len(result["unchanged"]) == 1
