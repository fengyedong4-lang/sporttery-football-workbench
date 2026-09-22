from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..schemas import Fixture
from .play_logic import aggregate_difference_probabilities, compatible, unique_pick


ANALYSIS_METHOD = "recent_form_poisson_v1"
ANALYSIS_LABEL = "近期赛果统计基线（非专属训练模型）"
LOOKBACK_DAYS = 180
HALF_LIFE_DAYS = 45
MAX_RECENT_MATCHES = 10
MIN_COMPARABLE_MATCHES = 3
MIN_PICK_MARGIN = 0.03
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")


def _dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _plain_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _empty_stats() -> dict[str, Any]:
    return {
        "sample_size": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "goals_for": 0,
        "goals_against": 0,
        "home_matches": 0,
        "away_matches": 0,
        "same_role_matches": 0,
        "different_role_matches": 0,
        "exact_goal_differences": {},
        "competition_season_groups": {},
    }


def _base_result(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "analysis_status": "insufficient_evidence",
        "analysis_method": ANALYSIS_METHOD,
        "analysis_label": ANALYSIS_LABEL,
        "analysis_summary": "可核验证据不足，未形成统计分析方向。",
        "analysis_result": None,
        "analysis_handicap_result": None,
        "raw_probabilities": None,
        "probability_status": "未生成：证据不足",
        "confidence": {"result": "低", "handicap_result": None},
        "risk": "高",
        "supporting_evidence": [],
        "major_counterevidence": [],
        "counterevidence_actions": [],
        "missing": _dedupe_strings(list(bundle.get("missing", []))),
        "evidence_stats": {"home": _empty_stats(), "away": _empty_stats()},
        "method_parameters": {
            "lookback_days": LOOKBACK_DAYS,
            "half_life_days": HALF_LIFE_DAYS,
            "recent_match_limit_per_team": MAX_RECENT_MATCHES,
            "minimum_matches_per_team_in_common_group": MIN_COMPARABLE_MATCHES,
        },
        "matched_evidence_ids": [],
        "freeze_eligible": False,
    }


def _blocked(bundle: dict[str, Any], reason: str) -> dict[str, Any]:
    result = _base_result(bundle)
    result.update(
        {
            "analysis_status": "blocked",
            "analysis_summary": f"分析已阻止：{reason}",
            "probability_status": "未生成：分析条件不合法",
            "major_counterevidence": [reason],
            "counterevidence_actions": [{"evidence": reason, "effect": "不生成任何分析选项或概率"}],
        }
    )
    result["missing"] = _dedupe_strings(result["missing"] + [reason])
    return result


def _source_ids(bundle: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for source in bundle.get("sources", []):
        if isinstance(source, dict) and isinstance(source.get("source_id"), str):
            source_id = source["source_id"].strip()
            if source_id:
                ids.add(source_id)
    return ids


def _row_time(row: dict[str, Any], cutoff: datetime) -> tuple[datetime | None, float | None]:
    """Return a sortable time and age in days, rejecting ambiguous same-day rows."""
    kickoff = _aware_datetime(row.get("kickoff_time"))
    if kickoff is not None:
        if kickoff >= cutoff:
            return None, None
        age_days = (cutoff.astimezone(timezone.utc) - kickoff.astimezone(timezone.utc)).total_seconds() / 86400
        if age_days < 0 or age_days > LOOKBACK_DAYS:
            return None, None
        return kickoff.astimezone(timezone.utc), age_days

    if row.get("time_precision") != "date":
        return None, None
    match_date = _plain_date(row.get("match_date"))
    if match_date is None:
        return None, None
    cutoff_date = cutoff.astimezone(SHANGHAI).date()
    # With date precision there is no safe ordering within the cutoff date.
    if match_date >= cutoff_date or match_date < cutoff_date - timedelta(days=LOOKBACK_DAYS):
        return None, None
    age_days = float((cutoff_date - match_date).days)
    return datetime.combine(match_date, datetime.min.time(), tzinfo=SHANGHAI).astimezone(timezone.utc), age_days


def _valid_score(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _filter_team_rows(
    rows: Any,
    *,
    team_id: str,
    current_role: str,
    cutoff: datetime,
    valid_source_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(rows, list):
        return [], ["近期赛果不是列表"]

    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected: list[str] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        match_id = raw.get("match_id")
        source_id = raw.get("source_id")
        if not isinstance(match_id, str) or not match_id.strip():
            continue
        if not isinstance(source_id, str) or source_id not in valid_source_ids:
            continue
        home_id = raw.get("home_team_id")
        away_id = raw.get("away_team_id")
        if home_id == away_id or team_id not in {home_id, away_id}:
            continue
        if raw.get("status") != "completed_90" or raw.get("score_basis") != "90_minutes":
            continue
        if raw.get("is_friendly") is not False:
            continue
        if not _valid_score(raw.get("home_goals_90")) or not _valid_score(raw.get("away_goals_90")):
            continue
        competition_id = raw.get("competition_id")
        season_id = raw.get("season_id")
        if not isinstance(competition_id, str) or not competition_id.strip():
            continue
        if not isinstance(season_id, str) or not season_id.strip():
            continue
        row_time, age_days = _row_time(raw, cutoff)
        if row_time is None or age_days is None:
            continue

        role = "home" if home_id == team_id else "away"
        goals_for = raw["home_goals_90"] if role == "home" else raw["away_goals_90"]
        goals_against = raw["away_goals_90"] if role == "home" else raw["home_goals_90"]
        candidates[match_id.strip()].append(
            {
                "match_id": match_id.strip(),
                "source_id": source_id,
                "competition": raw.get("competition", ""),
                "competition_id": competition_id.strip(),
                "season_id": season_id.strip(),
                "role": role,
                "same_role": role == current_role,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "difference": goals_for - goals_against,
                "sort_time": row_time,
                "age_days": age_days,
            }
        )

    accepted: list[dict[str, Any]] = []
    for match_id, versions in candidates.items():
        fingerprints = {
            (
                row["competition_id"],
                row["season_id"],
                row["role"],
                row["goals_for"],
                row["goals_against"],
                row["sort_time"],
            )
            for row in versions
        }
        if len(fingerprints) != 1:
            rejected.append(f"比赛{match_id}存在冲突重复记录，已全部排除")
            continue
        accepted.append(versions[0])

    accepted.sort(key=lambda row: (row["sort_time"], row["match_id"]), reverse=True)
    return accepted[:MAX_RECENT_MATCHES], rejected


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = _empty_stats()
    groups: dict[str, int] = defaultdict(int)
    differences: dict[int, int] = defaultdict(int)
    for row in rows:
        difference = row["difference"]
        result["sample_size"] += 1
        result["goals_for"] += row["goals_for"]
        result["goals_against"] += row["goals_against"]
        result["same_role_matches" if row["same_role"] else "different_role_matches"] += 1
        result[f"{row['role']}_matches"] += 1
        if difference > 0:
            result["wins"] += 1
        elif difference == 0:
            result["draws"] += 1
        else:
            result["losses"] += 1
        differences[difference] += 1
        groups[f"{row['competition_id']}::{row['season_id']}"] += 1
    result["exact_goal_differences"] = dict(sorted(differences.items()))
    result["competition_season_groups"] = dict(sorted(groups.items()))
    return result


def _select_common_group(
    home_rows: list[dict[str, Any]], away_rows: list[dict[str, Any]]
) -> tuple[tuple[str, str] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    home_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    away_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in home_rows:
        home_groups[(row["competition_id"], row["season_id"])].append(row)
    for row in away_rows:
        away_groups[(row["competition_id"], row["season_id"])].append(row)
    eligible = [
        key
        for key in home_groups.keys() & away_groups.keys()
        if len(home_groups[key]) >= MIN_COMPARABLE_MATCHES
        and len(away_groups[key]) >= MIN_COMPARABLE_MATCHES
    ]
    if not eligible:
        return None, [], []
    key = max(
        eligible,
        key=lambda item: (
            len(home_groups[item]) + len(away_groups[item]),
            min(len(home_groups[item]), len(away_groups[item])),
            item,
        ),
    )
    return key, home_groups[key], away_groups[key]


def _weighted_mean(rows: list[dict[str, Any]], field: str) -> float:
    weights = [math.exp(-math.log(2) * row["age_days"] / HALF_LIFE_DAYS) for row in rows]
    total_weight = sum(weights)
    return sum(weight * row[field] for weight, row in zip(weights, rows, strict=True)) / total_weight


def _poisson_probabilities(rate: float, limit: int = 24) -> list[float]:
    if rate == 0:
        return [1.0] + [0.0] * limit
    values = [math.exp(-rate)]
    for goals in range(1, limit + 1):
        values.append(values[-1] * rate / goals)
    return values


def _difference_probabilities(home_rate: float, away_rate: float) -> dict[int, float]:
    home = _poisson_probabilities(home_rate)
    away = _poisson_probabilities(away_rate)
    differences: dict[int, float] = defaultdict(float)
    for home_goals, home_probability in enumerate(home):
        for away_goals, away_probability in enumerate(away):
            differences[home_goals - away_goals] += home_probability * away_probability
    covered = sum(differences.values())
    if not math.isfinite(covered) or covered <= 0:
        raise ValueError("概率计算失败")
    return {difference: probability / covered for difference, probability in differences.items()}


def _top_margin(probabilities: dict[str, float]) -> float:
    ordered = sorted(probabilities.values(), reverse=True)
    return ordered[0] - ordered[1]


def _confidence(probabilities: dict[str, float] | None, sample_size: int, pick: str | None) -> str | None:
    if probabilities is None:
        return None
    if pick is None:
        return "低"
    return "中低" if sample_size >= 8 and _top_margin(probabilities) >= 0.08 else "低"


def analyze_evidence(
    fixture: Fixture, bundle: dict[str, Any], *, as_of: datetime | None = None
) -> dict[str, Any]:
    """Build a cautious, local-only recent-results baseline from traceable evidence."""
    if not isinstance(bundle, dict):
        return _blocked({}, "证据包格式无效")
    if fixture.kickoff_time.tzinfo is None or fixture.kickoff_time.utcoffset() is None:
        return _blocked(bundle, "比赛开球时间必须包含时区")
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return _blocked(bundle, "分析时间必须包含时区")
    if as_of >= fixture.kickoff_time:
        return _blocked(bundle, "比赛已开赛或分析时间不早于开球时间")
    if bundle.get("identity_verified") is not True:
        return _blocked(bundle, "主客队身份映射未核实")

    cutoff = min(as_of, fixture.kickoff_time)
    teams = bundle.get("teams")
    if not isinstance(teams, dict):
        result = _base_result(bundle)
        result["missing"] = _dedupe_strings(result["missing"] + ["主客队近期赛果"])
        return result
    home = teams.get("home")
    away = teams.get("away")
    if not isinstance(home, dict) or not isinstance(away, dict):
        result = _base_result(bundle)
        result["missing"] = _dedupe_strings(result["missing"] + ["主客队近期赛果"])
        return result
    home_id = home.get("team_id")
    away_id = away.get("team_id")
    if (
        not isinstance(home_id, str)
        or not home_id.strip()
        or not isinstance(away_id, str)
        or not away_id.strip()
        or home_id.strip() == away_id.strip()
    ):
        return _blocked(bundle, "主客队精确team_id缺失或冲突")

    valid_source_ids = _source_ids(bundle)
    home_rows, home_rejections = _filter_team_rows(
        home.get("recent_matches"),
        team_id=home_id.strip(),
        current_role="home",
        cutoff=cutoff,
        valid_source_ids=valid_source_ids,
    )
    away_rows, away_rejections = _filter_team_rows(
        away.get("recent_matches"),
        team_id=away_id.strip(),
        current_role="away",
        cutoff=cutoff,
        valid_source_ids=valid_source_ids,
    )
    home_stats = _stats(home_rows)
    away_stats = _stats(away_rows)
    result = _base_result(bundle)
    result["evidence_stats"] = {"home": home_stats, "away": away_stats}
    result["matched_evidence_ids"] = sorted(
        {row["source_id"] for row in home_rows + away_rows}
    )
    warnings = _dedupe_strings(
        list(bundle.get("warnings", [])) + home_rejections + away_rejections
    )

    result["supporting_evidence"] = [
        f"主队有效样本{home_stats['sample_size']}场：{home_stats['wins']}胜{home_stats['draws']}平{home_stats['losses']}负，进{home_stats['goals_for']}球失{home_stats['goals_against']}球。",
        f"客队有效样本{away_stats['sample_size']}场：{away_stats['wins']}胜{away_stats['draws']}平{away_stats['losses']}负，进{away_stats['goals_for']}球失{away_stats['goals_against']}球。",
    ]

    common_key, model_home_rows, model_away_rows = _select_common_group(home_rows, away_rows)
    if common_key is None:
        home_keys = {
            (row["competition_id"], row["season_id"]) for row in home_rows
        }
        away_keys = {
            (row["competition_id"], row["season_id"]) for row in away_rows
        }
        comparison_status = "cross_level" if home_rows and away_rows and not (home_keys & away_keys) else "insufficient"
        reason = (
            "双方有效样本没有共同competition_id+season_id，可能存在竞赛层级差异，禁止直接比较。"
            if comparison_status == "cross_level"
            else "双方共同competition_id+season_id下未同时达到各3场，禁止用旧样本或跨赛事样本补齐。"
        )
        result["analysis_summary"] = (
            f"仅完成双方独立近期事实摘要；{reason}"
        )
        result["method_parameters"].update(
            {"comparison_status": comparison_status, "common_competition_season": None}
        )
        result["major_counterevidence"] = [reason]
        if min(home_stats["sample_size"], away_stats["sample_size"]) < MIN_COMPARABLE_MATCHES:
            result["major_counterevidence"].append("精确球队ID过滤后样本稀疏，存在换代与阵容连续性风险。")
        result["major_counterevidence"].extend(warnings)
        result["counterevidence_actions"] = [
            {"evidence": item, "effect": "保持高风险，不生成概率或分析选项"}
            for item in result["major_counterevidence"]
        ]
        result["missing"] = _dedupe_strings(
            result["missing"] + ["双方同一竞赛同一赛季且各至少3场的可比样本"]
        )
        return result

    home_gf = _weighted_mean(model_home_rows, "goals_for")
    home_ga = _weighted_mean(model_home_rows, "goals_against")
    away_gf = _weighted_mean(model_away_rows, "goals_for")
    away_ga = _weighted_mean(model_away_rows, "goals_against")
    lambda_home = (home_gf + away_ga) / 2
    lambda_away = (away_gf + home_ga) / 2
    differences = _difference_probabilities(lambda_home, lambda_away)
    result_probs, handicap_probs = aggregate_difference_probabilities(
        differences, fixture.official_handicap
    )
    result_pick = unique_pick(result_probs)
    result_margin = _top_margin(result_probs)
    balanced = abs(lambda_home - lambda_away) <= max(
        0.25, 0.20 * max(lambda_home, lambda_away, 0.25)
    )
    if result_pick == "平" and (not balanced or result_margin < MIN_PICK_MARGIN):
        result_pick = None
        result_reason = "平局虽为概率最高项，但缺少明确的攻防均衡事实或领先次高项不足3个百分点。"
    else:
        result_reason = None

    handicap_pick = unique_pick(handicap_probs) if handicap_probs is not None else None
    handicap_reason: str | None = None
    exact_boundary_paths = 0
    if fixture.official_handicap is None:
        handicap_pick = None
        handicap_reason = "官方让球缺失，仅保留胜平负统计分析。"
    elif handicap_probs is not None and handicap_pick == "让平":
        boundary = -fixture.official_handicap
        exact_boundary_paths = sum(row["difference"] == boundary for row in model_home_rows)
        exact_boundary_paths += sum(-row["difference"] == boundary for row in model_away_rows)
        if _top_margin(handicap_probs) < MIN_PICK_MARGIN or exact_boundary_paths < 2:
            handicap_pick = None
            handicap_reason = "让平缺少明显概率优势或至少2场同口径精确净胜球边界路径。"

    if (
        result_pick is not None
        and handicap_pick is not None
        and fixture.official_handicap is not None
        and not compatible(result_pick, handicap_pick, fixture.official_handicap)
    ):
        handicap_pick = None
        handicap_reason = "胜平负与让球玩法的最高概率组合不相容，未调整任一方向强行凑配。"

    sample_size = len(model_home_rows) + len(model_away_rows)
    result.update(
        {
            "analysis_status": "available",
            "analysis_summary": "已按双方同一竞赛、同一赛季的近期赛果生成统计候选；该结果不是专属训练模型，也未校准对手强度或阵容。",
            "analysis_result": result_pick,
            "analysis_handicap_result": handicap_pick,
            "raw_probabilities": {
                "result": result_probs,
                "handicap_result": handicap_probs,
            },
            "probability_status": "原始近期赛果统计值，未训练、未校准",
            "confidence": {
                "result": _confidence(result_probs, sample_size, result_pick),
                "handicap_result": _confidence(handicap_probs, sample_size, handicap_pick),
            },
        }
    )
    result["method_parameters"].update(
        {
            "comparison_status": "comparable",
            "common_competition_season": {
                "competition_id": common_key[0],
                "season_id": common_key[1],
                "home_matches": len(model_home_rows),
                "away_matches": len(model_away_rows),
            },
            "lambda_home": lambda_home,
            "lambda_away": lambda_away,
            "result_top_margin": result_margin,
            "draw_balance_supported": balanced,
            "handicap_exact_boundary_paths": exact_boundary_paths,
        }
    )
    result["supporting_evidence"].extend(
        [
            f"共同竞赛赛季样本为主队{len(model_home_rows)}场、客队{len(model_away_rows)}场。",
            f"45天半衰期加权后，主队进攻均值{home_gf:.2f}、防守失球均值{home_ga:.2f}；客队进攻均值{away_gf:.2f}、防守失球均值{away_ga:.2f}。",
        ]
    )
    counterevidence = [
        "方法未校准对手强度、伤停、首发、赛程和阵容换代。",
        "这是近期赛果统计基线，不是该联赛或球队的专属训练模型。",
    ]
    if len(model_home_rows) < 5 or len(model_away_rows) < 5:
        counterevidence.append("同口径可比样本仍偏小，方向稳定性有限。")
    if home_stats["same_role_matches"] < home_stats["different_role_matches"]:
        counterevidence.append("主队有效样本以异主客角色为主，当前主场适配证据有限。")
    if away_stats["same_role_matches"] < away_stats["different_role_matches"]:
        counterevidence.append("客队有效样本以异主客角色为主，当前客场适配证据有限。")
    if result_reason:
        counterevidence.append(result_reason)
    if handicap_reason:
        counterevidence.append(handicap_reason)
    counterevidence.extend(warnings)
    result["major_counterevidence"] = _dedupe_strings(counterevidence)
    result["counterevidence_actions"] = [
        {
            "evidence": item,
            "effect": (
                "对应玩法不输出分析选项"
                if item in {result_reason, handicap_reason}
                else "维持高风险并限制置信度最高为中低"
            ),
        }
        for item in result["major_counterevidence"]
    ]
    if fixture.official_handicap is None:
        result["missing"] = _dedupe_strings(result["missing"] + ["官方让球"])
    return result
