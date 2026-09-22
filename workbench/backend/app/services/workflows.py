from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schemas import Fixture, ResultInput
from .history import atomic_json_write
from .model import GoalModel, ModelError, UnknownTeamError
from .evidence_workflow import fill_evidence_fallbacks
from .play_logic import aggregate_difference_probabilities, compatible, unique_pick
from .prefilter import RulePrefilter, compact_rule


CONFIDENCE_THRESHOLDS = {
    "高": 0.62,
    "中高": 0.52,
    "中": 0.44,
    "中低": 0.37,
    "低": 0.0,
}


def confidence(probability: float, *, covered: bool) -> str:
    if not covered:
        return "低"
    for label, threshold in CONFIDENCE_THRESHOLDS.items():
        if probability >= threshold:
            return label
    return "低"


def _official_eligible(play: Any) -> bool:
    return play.status == "销售中" and all(
        value is not None for value in (play.home, play.draw, play.away)
    )


def run_daily_prediction(
    fixtures: list[Fixture],
    *,
    model_path: Path | None = None,
    model_paths: dict[str, Path] | None = None,
    team_aliases: dict[str, dict[str, str]] | None = None,
    neutral_competitions: set[str] | None = None,
    prefilter: RulePrefilter,
    rule_budget: int,
    output_dir: Path,
    evidence_dir: Path | None = None,
    llm_enabled: bool = False,
    llm_model: str = "gpt-6-astra",
    llm_effort: str = "xhigh",
    llm_timeout_seconds: int = 360,
    dynamic_research_enabled: bool = False,
) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    if (model_path is None) == (model_paths is None):
        raise ValueError("必须且只能指定单一模型或按赛事模型映射")
    loaded_models: dict[Path, GoalModel] = {}
    rows: list[dict[str, Any]] = []
    total_rule_bytes = 0
    unique_rules: dict[str, dict[str, Any]] = {}
    fallback_items: list[tuple[Fixture, dict]] = []
    for fixture in fixtures:
        row: dict[str, Any] = {
            "sequence": fixture.sequence,
            "match_id": fixture.match_id,
            "match_number": fixture.match_number,
            "competition": fixture.competition,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "kickoff_time": fixture.kickoff_time.isoformat(),
            "official_handicap": fixture.official_handicap,
            "status": "候选预测，尚未正式冻结",
            "lineup_status": fixture.lineup_status,
            "source_snapshot_id": fixture.source_snapshot_id,
        }
        coverage_missing = False
        can_collect = True
        try:
            selected_path = model_path if model_path is not None else (model_paths or {}).get(fixture.competition)
            if selected_path is None:
                coverage_missing = True
                raise ModelError(f"没有覆盖赛事的模型: {fixture.competition}")
            if selected_path not in loaded_models:
                loaded_models[selected_path] = GoalModel.load(selected_path)
            model = loaded_models[selected_path]
            if model.artifact_schema_version < 2:
                raise ModelError("旧模型缺少整体进球基准，已禁止用于新预测")
            if fixture.competition != model.competition:
                coverage_missing = True
                raise ModelError(f"模型不覆盖联赛: {fixture.competition}")
            aliases = (team_aliases or {}).get(fixture.competition, {})
            home_team = aliases.get(fixture.home_team, fixture.home_team)
            away_team = aliases.get(fixture.away_team, fixture.away_team)
            neutral = fixture.neutral or fixture.competition in (neutral_competitions or set())
            if fixture.kickoff_time.date() <= model.training_cutoff:
                raise ModelError(
                    f"训练截止日{model.training_cutoff.isoformat()}不早于比赛日，拒绝未来数据泄漏"
                )
            if fixture.kickoff_time.tzinfo is None or datetime.now(timezone.utc) >= fixture.kickoff_time:
                raise ModelError("比赛已开赛或开球时间无时区，拒绝补作赛前预测")
            prediction = model.predict(home_team, away_team, neutral=neutral)
            differences = {int(key): value for key, value in prediction["difference_probabilities"].items()}
            result_probs, handicap_probs = aggregate_difference_probabilities(
                differences, fixture.official_handicap
            )
            result_pick = unique_pick(result_probs)
            handicap_pick = unique_pick(handicap_probs) if handicap_probs else None
            result_valid = _official_eligible(fixture.result_play)
            handicap_valid = (
                fixture.official_handicap is not None
                and _official_eligible(fixture.handicap_play)
            )
            if result_valid and handicap_valid and handicap_pick and not compatible(
                result_pick, handicap_pick, fixture.official_handicap
            ):
                consistency = "边际最高概率组合不相容，必须人工复核"
                freeze_eligible = False
            else:
                consistency = "通过" if (not handicap_pick or fixture.official_handicap is None or compatible(result_pick, handicap_pick, fixture.official_handicap)) else "未通过"
                freeze_eligible = result_valid and (not handicap_valid or consistency == "通过")
            matched = prefilter.match(
                {
                    "competition": fixture.competition,
                    "official_handicap": fixture.official_handicap,
                },
                budget=rule_budget,
            )
            for rule in matched:
                compact = compact_rule(rule)
                unique_rules[str(compact["id"])] = compact
            row.update(
                {
                    "model_status": "available",
                    "model_version": selected_path.stem,
                    "model_training_matches": model.validation.get("matches"),
                    "training_cutoff": model.training_cutoff.isoformat(),
                    "resolved_teams": {"home": home_team, "away": away_team},
                    "neutral": neutral,
                    "probability_status": "原始研究值，未校准",
                    "result": result_pick if result_valid else None,
                    "result_status": "已确认" if result_valid else ("待核实" if fixture.result_play.status == "销售中" else fixture.result_play.status),
                    "handicap_result": handicap_pick if handicap_valid else None,
                    "handicap_status": "已确认" if handicap_valid else ("官方数据暂未获取" if fixture.official_handicap is None else ("待核实" if fixture.handicap_play.status == "销售中" else fixture.handicap_play.status)),
                    "raw_probabilities": {
                        "result": result_probs,
                        "handicap_result": handicap_probs,
                    },
                    "confidence": {
                        "result": confidence(max(result_probs.values()), covered=result_valid),
                        "handicap_result": confidence(max(handicap_probs.values()), covered=handicap_valid) if handicap_probs else None,
                    },
                    "risk": "高" if fixture.lineup_status not in {"已确认", "不适用"} else "中",
                    "consistency": consistency,
                    "freeze_eligible": freeze_eligible,
                    "tail_error": prediction["tail_error"],
                    "matched_rule_ids": [compact_rule(rule)["id"] for rule in matched],
                    "major_counterevidence": ["首发未确认"] if fixture.lineup_status != "已确认" else [],
                    "missing": ["确认首发"] if fixture.lineup_status != "已确认" else [],
                }
            )
            row["dedicated_model"] = {key: row.get(key) for key in (
                "model_version", "model_training_matches", "training_cutoff", "resolved_teams",
                "raw_probabilities", "result", "handicap_result", "confidence")}
        except ModelError as exc:
            row.update(
                {
                    "model_status": "unavailable",
                    "reason": str(exc),
                    "result": None,
                    "handicap_result": None,
                    "freeze_eligible": False,
                }
            )
            can_collect = coverage_missing or isinstance(exc, UnknownTeamError)
        # Every valid pre-match request refreshes dynamic team information, even
        # when a registered quantitative model exists. Integrity failures never
        # gain a back door through this lane.
        if evidence_dir is not None and can_collect:
            if fixture.kickoff_time.tzinfo and datetime.now(timezone.utc) < fixture.kickoff_time:
                fallback_items.append((fixture, row))
        rows.append(row)
    evidence_report = fill_evidence_fallbacks(
        fallback_items, evidence_dir=evidence_dir, llm_enabled=llm_enabled,
        llm_model=llm_model, llm_effort=llm_effort, llm_timeout_seconds=llm_timeout_seconds,
        dynamic_research_enabled=dynamic_research_enabled,
    ) if evidence_dir is not None else {}
    compact_rules = list(unique_rules.values())
    total_rule_bytes = len(json.dumps(compact_rules, ensure_ascii=False).encode("utf-8"))
    document = {
        "schema_version": 1,
        "mode": "A",
        "run_id": run_id,
        "created_at": created_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "official_fetch_performed": False,
        "llm_calls": 0,
        "llm_usage": {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None},
        "matches": rows,
        "prefilter_report": {
            "fixture_count": len(fixtures),
            "returned_fixture_count": len(rows),
            "unique_rules": len(compact_rules),
            "compact_rule_bytes": total_rule_bytes,
            "duplicate_rules_sent": 0,
        },
        "matched_rules": compact_rules,
        **evidence_report,
    }
    output_path = output_dir / f"{run_id}.json"
    atomic_json_write(output_path, document)
    document["saved_path"] = str(output_path)
    return document


def settle_review(prediction: dict[str, Any], results: list[ResultInput]) -> dict[str, Any]:
    by_id = {result.match_id: result for result in results}
    settled: list[dict[str, Any]] = []
    denominators = {"result": 0, "handicap_result": 0}
    hits = {"result": 0, "handicap_result": 0}
    excluded: list[dict[str, str]] = []
    for row in prediction.get("matches", []):
        result = by_id.get(row.get("match_id"))
        if not result or result.status != "已完赛" or not result.verified_90_minutes:
            excluded.append({"match_id": row.get("match_id"), "reason": "90分钟赛果未确认"})
            continue
        if result.home_goals_90 is None or result.away_goals_90 is None:
            excluded.append({"match_id": row.get("match_id"), "reason": "比分缺失"})
            continue
        difference = result.home_goals_90 - result.away_goals_90
        actual_result = "胜" if difference > 0 else "平" if difference == 0 else "负"
        handicap = row.get("official_handicap")
        actual_handicap = None
        if isinstance(handicap, int):
            adjusted = difference + handicap
            actual_handicap = "让胜" if adjusted > 0 else "让平" if adjusted == 0 else "让负"
        item = {
            "match_id": row.get("match_id"),
            "actual_result": actual_result,
            "actual_handicap_result": actual_handicap,
            "fact_confirmation": "90分钟事实已确认",
            "official_settlement": "已核实" if result.official_settlement_verified else "待核实",
        }
        for play, actual in (("result", actual_result), ("handicap_result", actual_handicap)):
            selection = row.get(play)
            if selection is not None and actual is not None:
                denominators[play] += 1
                if selection == actual:
                    hits[play] += 1
                item[f"hit_{play}"] = selection == actual
        settled.append(item)
    return {
        "settled": settled,
        "hits": hits,
        "denominators": denominators,
        "excluded": excluded,
        "rules_modified": False,
    }
