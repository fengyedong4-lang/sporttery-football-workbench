"""Auditable auxiliary market context. Never rewrites official fields or probabilities."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, date
import math


POLICY = {
    "version": "fundamentals-first-v1",
    "normal_market_reference": 0.20,
    "frequent_upset_market_reference": 0.30,
    "minimum_paired_matches": 8,
    "minimum_upsets": 3,
    "minimum_upset_rate": 0.35,
    "favorite_probability_floor": 0.50,
    "meaning": "定性证据参考预算，不是已校准概率混合系数",
}


def _time(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value if value.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _odds(row, market):
    keys = ("home", "draw", "away") if market == "european_1x2" else ("home", "away")
    odds = row.get("odds", row)
    if not all(type(odds.get(k)) in (float, int) and math.isfinite(odds[k]) and 1 < odds[k] < 1001 for k in keys):
        return None
    inverse = {k: 1 / odds[k] for k in keys}
    total = sum(inverse.values())
    return {k: inverse[k] / total for k in keys}


def build_market_context(fixture, research: dict, *, historical_rows: list[dict] | None = None) -> dict:
    """Consume only verified sources; missing history never activates the boost.

    historical_rows contract: match_id/team_id, kickoff_time, observed_at,
    source_id, source_verified, odds={home,draw,away}, actual_result=home/draw/away.
    No after-the-match odds or synthetic probability estimates are admissible.
    """
    now = datetime.now(timezone.utc)
    cutoff = min(now, fixture.kickoff_time) if fixture.kickoff_time.tzinfo else now
    sources = {s.get("source_id"): s for s in research.get("sources", [])
               if s.get("source_id") and (s.get("sha256") or s.get("content_sha256")) and s.get("url")}
    market = research.get("market") or {}
    observations = market.get("observations") or research.get("market_observations") or []
    groups = defaultdict(list)
    excluded = []
    for row in observations:
        kind = row.get("market_type")
        timestamp = _time(row.get("observed_at"))
        bookmaker = str(row.get("bookmaker") or "").strip()
        source = sources.get(row.get("source_id"))
        if (kind not in {"european_1x2", "asian_handicap"} or not timestamp or timestamp > cutoff
                or not bookmaker or not source or str(row.get("fixture_id", fixture.match_id)) != fixture.match_id):
            excluded.append("盘口身份、来源或赛前时间未核实")
            continue
        implied = _odds(row, kind)
        line = row.get("line")
        if kind == "asian_handicap" and (type(line) not in (int, float) or not math.isfinite(line) or abs(line) > 20):
            excluded.append("亚洲盘口线缺失或无效")
            continue
        if implied is None:
            excluded.append("十进制赔率缺失或无效")
            continue
        groups[(bookmaker, kind, line if kind == "asian_handicap" else None)].append((timestamp, row, implied))
    snapshots, changes, unchanged = [], [], []
    asian_lines = defaultdict(list)
    for (bookmaker, kind, line), values in groups.items():
        by_time = defaultdict(list)
        for value in values:
            by_time[value[0]].append(value)
        clean = []
        for timestamp, records in by_time.items():
            if len({tuple(sorted(r[2].items())) for r in records}) > 1:
                excluded.append("同书商同时间盘口冲突")
            else:
                clean.append(records[0])
        clean.sort(key=lambda item: item[0])
        if not clean:
            continue
        latest = clean[-1]
        snapshots.append({"bookmaker": bookmaker, "market_type": kind, "line": line,
                          "observed_at": latest[0].isoformat(), "source_id": latest[1]["source_id"],
                          "implied_probabilities": latest[2]})
        if kind == "asian_handicap":
            asian_lines[bookmaker].extend((stamp, line, item) for stamp, item, _ in clean)
        if len(clean) >= 2:
            earliest = clean[0]
            comparison = {"bookmaker": bookmaker, "market_type": kind, "line": line,
                            "from": earliest[0].isoformat(), "to": latest[0].isoformat(),
                            "source_ids": list(dict.fromkeys([earliest[1]["source_id"], latest[1]["source_id"]])),
                            "implied_probability_change": {k: latest[2][k]-earliest[2][k] for k in latest[2]}}
            (changes if any(abs(v) > 1e-9 for v in comparison["implied_probability_change"].values()) else unchanged).append(comparison)
    for bookmaker, values in asian_lines.items():
        values.sort(key=lambda value: value[0])
        earliest, latest = values[0], values[-1]
        if earliest[0] < latest[0] and earliest[1] != latest[1]:
            changes.append({"bookmaker": bookmaker, "market_type": "asian_handicap", "change_type": "line_change",
                            "from": earliest[0].isoformat(), "to": latest[0].isoformat(),
                            "from_line": earliest[1], "to_line": latest[1],
                            "source_ids": list(dict.fromkeys([earliest[2]["source_id"], latest[2]["source_id"]])),
                            "implied_probability_change": None,
                            "interpretation": "主队让球盘线变化；不同盘线价格不作同一事件概率比较"})
    paired = {}
    for row in historical_rows or []:
        observed, kickoff = _time(row.get("observed_at")), _time(row.get("kickoff_time"))
        probs = _odds(row, "european_1x2")
        # Explicit provider closing fields establish pre-match semantics but do
        # not invent a timestamp. Unknown/opening/generic columns cannot use it.
        closing_before = False
        try:
            closing_before = (row.get("closing_verified") is True and row.get("odds_type") == "closing_1x2"
                              and date(2025, 1, 1) <= date.fromisoformat(row["match_date"]) < cutoff.date()
                              and row.get("source_sha256") and row.get("odds_fields")
                              and all(k in {"B365CH", "B365CD", "B365CA", "PCH", "PCD", "PCA"} for k in row["odds_fields"]))
        except (ValueError, KeyError, TypeError):
            pass
        if (not row.get("source_verified") or not row.get("source_id") or not row.get("match_id")
                or not (closing_before or (observed and kickoff and observed < kickoff < cutoff)) or not probs
                or row.get("actual_result") not in probs):
            continue
        favorite = max(probs, key=probs.get)
        if favorite == "draw" or probs[favorite] < POLICY["favorite_probability_floor"]:
            continue
        paired[str(row["match_id"])] = {"upset": row["actual_result"] != favorite, "source_id": row["source_id"]}
    count = len(paired)
    upsets = sum(r["upset"] for r in paired.values())
    rate = upsets / count if count else None
    frequent = count >= POLICY["minimum_paired_matches"] and upsets >= POLICY["minimum_upsets"] and rate >= POLICY["minimum_upset_rate"]
    weight = POLICY["frequent_upset_market_reference"] if frequent else POLICY["normal_market_reference"]
    # No usable market information means zero actual market contribution.
    usable_text = any(f.get("dimension") in {"market", "盘口", "欧亚盘"} and f.get("source_ids") for f in research.get("facts", []))
    effective_weight = weight if snapshots or usable_text else 0.0
    return {"policy": POLICY, "status": "changes_verified" if changes else "unchanged_verified" if unchanged else "snapshot_only" if snapshots else "text_only" if usable_text else "unavailable",
            "snapshots": snapshots, "changes": changes, "unchanged": unchanged, "excluded": sorted(set(excluded)),
            "upset_profile": {"paired_matches": count, "upsets": upsets, "rate": rate, "frequent": frequent,
                              "definition": "赛前去水概率至少50%的非平热门未获胜；仅统计可信赛前赔率与赛果配对", "source_ids": sorted({r["source_id"] for r in paired.values()})},
            "reference_budget": {"fundamentals": 1-effective_weight, "market": effective_weight, "maximum_market": weight},
            "reason": "真实配对样本达到频繁爆冷门槛，辅助盘口参考上限升至30%" if frequent else "未证明频繁爆冷，保持基本面优先；无盘口资料时不使用市场信号",
            "probabilities_modified": False, "official_fields_modified": False}
