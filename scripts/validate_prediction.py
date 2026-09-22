#!/usr/bin/env python3
"""Read-only validator and selection helpers for prediction schema v4."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 4
PLAYS = ("result", "handicap_result")
RESULT_VALUES = {"胜", "平", "负"}
HANDICAP_VALUES = {"让胜", "让平", "让负"}
CONFIDENCE_VALUES = {"低", "中低", "中", "中高", "高"}
RISK_VALUES = {"低", "中", "高"}
OPENNESS_VALUES = {"低", "中低", "中", "中高", "高"}
PLAY_STATUS_VALUES = {"已确认", "未开售", "待核实", "无法获取", "未请求", "不适用"}
MATCH_STATUS_VALUES = {"待核实", "未开赛", "已完赛", "延期", "中止", "取消", "待补赛"}
NON_FINAL_MATCH_STATUSES = {"待核实", "未开赛", "延期", "中止", "取消", "待补赛"}
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VersionConflictError(ValueError):
    """Raised when the same match/version has different formal records."""


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.skipped


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or value.startswith("YYYY-"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _selection_matches(result: str, handicap_result: str, handicap: int) -> bool:
    """Return whether an integer goal difference d can satisfy both selections."""
    result_sign = {"胜": 1, "平": 0, "负": -1}[result]
    handicap_sign = {"让胜": 1, "让平": 0, "让负": -1}[handicap_result]
    limit = max(20, abs(handicap) + 5)
    for difference in range(-limit, limit + 1):
        plain = (difference > 0) - (difference < 0)
        adjusted = difference + handicap
        with_handicap = (adjusted > 0) - (adjusted < 0)
        if plain == result_sign and with_handicap == handicap_sign:
            return True
    return False


def _validate_probability_triplet(
    values: dict[str, Any], keys: tuple[str, str, str], path: str, report: ValidationReport, *, mode: str
) -> None:
    triplet = [values.get(key) for key in keys]
    if all(value is None for value in triplet):
        if mode == "formal" and not values.get("reason_unavailable"):
            report.errors.append(f"{path}: 三项概率全为null时必须填写reason_unavailable")
        return
    if not all(_is_number(value) for value in triplet):
        report.errors.append(f"{path}: 三项概率必须全部为0..100数值，或全部为null")
        return
    if any(value < 0 or value > 100 for value in triplet):
        report.errors.append(f"{path}: 概率必须在0..100范围内")
    if not math.isclose(sum(triplet), 100.0, abs_tol=1e-6):
        report.errors.append(f"{path}: 三项概率合计必须为100")


def _validate_pre_match_time(value: Any, kickoff: datetime | None, path: str, report: ValidationReport, required: bool) -> datetime | None:
    parsed = _parse_time(value)
    if parsed is None:
        if required or value is not None:
            report.errors.append(f"{path}: 缺少有效的带时区时间")
        return None
    if kickoff is not None and parsed >= kickoff:
        report.errors.append(f"{path}: 必须早于开赛时间")
    return parsed


def _iter_source_times(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in {"published_at", "checked_at", "estimated_at", "assessed_at", "saved_at"}:
                yield child_path, child
            yield from _iter_source_times(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_source_times(child, f"{path}[{index}]")


def _validate_before_source(before: dict[str, Any], path: str, base_dir: Path | None, report: ValidationReport, mode: str) -> None:
    source_file = before.get("source_file")
    source_hash = before.get("source_sha256")
    if mode != "formal" and source_file is None and source_hash is None:
        return
    if not isinstance(source_file, str) or not source_file:
        report.errors.append(f"{path}.source_file: formal记录必须引用独立判断落盘文件")
        return
    if not isinstance(source_hash, str) or not HASH_RE.fullmatch(source_hash):
        report.errors.append(f"{path}.source_sha256: 必须是64位SHA-256")
        return
    if base_dir is None:
        report.errors.append(f"{path}.source_file: 未提供基准目录，无法核验文件存在及hash")
        return
    candidate = Path(source_file)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if not candidate.is_file():
        report.errors.append(f"{path}.source_file: 引用文件不存在")
        return
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual.lower() != source_hash.lower():
        report.errors.append(f"{path}.source_sha256: 与引用文件不一致")


def _validate_match(match: dict[str, Any], index: int, prediction_set: dict[str, Any], mode: str, base_dir: Path | None, report: ValidationReport) -> None:
    path = f"matches[{index}]"
    kickoff = _parse_time(match.get("kickoff_time"))
    if kickoff is None and mode == "formal":
        report.errors.append(f"{path}.kickoff_time: formal记录必须有带时区开赛时间")

    match_status = match.get("match_status")
    if match_status not in MATCH_STATUS_VALUES:
        report.errors.append(f"{path}.match_status: 非法枚举 {match_status!r}")

    prediction = match.get("prediction")
    if not isinstance(prediction, dict):
        report.errors.append(f"{path}.prediction: 缺少对象")
        return

    if any(prediction.get(key) is not None for key in ("predicted_score", "predicted_total_goals", "score", "total_goals")):
        report.errors.append(f"{path}.prediction: v4禁止保存比分或总进球预测")
    if any(play in {"score", "total_goals"} for play in prediction.get("prediction_plays", [])):
        report.errors.append(f"{path}.prediction.prediction_plays: 禁止列入比分或总进球")

    plays = prediction.get("prediction_plays")
    if not isinstance(plays, list) or len(plays) != len(set(plays)):
        report.errors.append(f"{path}.prediction.prediction_plays: 必须是无重复列表")
        plays = []
    invalid_plays = set(plays) - set(PLAYS)
    if invalid_plays:
        report.errors.append(f"{path}.prediction.prediction_plays: 非法玩法 {sorted(invalid_plays)}")

    play_statuses = prediction.get("play_status")
    if not isinstance(play_statuses, dict):
        report.errors.append(f"{path}.prediction.play_status: 缺少对象")
        return

    values = {
        "result": ("predicted_result", RESULT_VALUES),
        "handicap_result": ("predicted_handicap_result", HANDICAP_VALUES),
    }
    official_data = match.get("official_data") if isinstance(match.get("official_data"), dict) else {}
    for play, (legacy_key, allowed) in values.items():
        status_entry = play_statuses.get(play)
        if not isinstance(status_entry, dict):
            report.errors.append(f"{path}.prediction.play_status.{play}: 缺少对象")
            continue
        status = status_entry.get("status")
        selection = status_entry.get("selection")
        legacy_selection = prediction.get(legacy_key)
        if status not in PLAY_STATUS_VALUES:
            report.errors.append(f"{path}.prediction.play_status.{play}.status: 非法枚举 {status!r}")
        if selection is not None and selection not in allowed:
            report.errors.append(f"{path}.prediction.play_status.{play}.selection: 非法唯一选项 {selection!r}")
        if legacy_selection != selection:
            report.errors.append(f"{path}.prediction.{legacy_key}: 必须与play_status.{play}.selection一致")
        if status == "已确认":
            if selection not in allowed:
                report.errors.append(f"{path}.prediction.play_status.{play}: 已确认玩法必须有一个合法唯一选项")
            if play not in plays:
                report.errors.append(f"{path}.prediction.prediction_plays: 已确认玩法{play}必须列入")
            if status_entry.get("confidence") not in CONFIDENCE_VALUES:
                report.errors.append(f"{path}.prediction.play_status.{play}.confidence: 缺少合法置信度")
        else:
            if selection is not None or play in plays:
                report.errors.append(f"{path}.prediction.play_status.{play}: 非已确认状态不得保存正式选项")
            if mode == "formal" and not status_entry.get("reason_unavailable"):
                report.errors.append(f"{path}.prediction.play_status.{play}: 缺失玩法必须说明原因")

        official_key = "spf" if play == "result" else "handicap"
        official_play = official_data.get(official_key) if isinstance(official_data.get(official_key), dict) else {}
        official_status = str(official_play.get("status", ""))
        if ("未开售" in official_status or "待开售" in official_status) and (selection is not None or play in plays):
            report.errors.append(f"{path}.{official_key}: 未开售玩法不得有正式选项")

    result = prediction.get("predicted_result")
    handicap_result = prediction.get("predicted_handicap_result")
    official_handicap = ((official_data.get("handicap") or {}).get("official_handicap") if isinstance(official_data.get("handicap"), dict) else None)
    assessment = prediction.get("handicap_assessment") if isinstance(prediction.get("handicap_assessment"), dict) else {}
    if handicap_result is not None:
        if not _is_int(official_handicap):
            report.errors.append(f"{path}.official_data.handicap.official_handicap: 未知或非整数让球不得有让球选项")
        if assessment.get("official_handicap") != official_handicap:
            report.errors.append(f"{path}.prediction.handicap_assessment.official_handicap: 必须与官方让球一致")
    if assessment.get("expression") != "d+h":
        report.errors.append(f"{path}.prediction.handicap_assessment.expression: 必须使用d+h")
    if result in RESULT_VALUES and handicap_result in HANDICAP_VALUES and _is_int(official_handicap):
        if not _selection_matches(result, handicap_result, official_handicap):
            report.errors.append(f"{path}.prediction: 两项结果不存在共同满足的整数净胜球差d")

    probabilities = prediction.get("three_way_probabilities")
    if not isinstance(probabilities, dict):
        report.errors.append(f"{path}.prediction.three_way_probabilities: 缺少对象")
    else:
        _validate_probability_triplet(probabilities, ("home", "draw", "away"), f"{path}.prediction.three_way_probabilities", report, mode=mode)
        if mode == "formal" and any(probabilities.get(key) is not None for key in ("home", "draw", "away")):
            if not probabilities.get("method"):
                report.errors.append(f"{path}.prediction.three_way_probabilities.method: 数值概率必须说明方法")
            _validate_pre_match_time(probabilities.get("evidence_checked_at"), kickoff, f"{path}.prediction.three_way_probabilities.evidence_checked_at", report, True)
            _validate_pre_match_time(probabilities.get("estimated_at"), kickoff, f"{path}.prediction.three_way_probabilities.estimated_at", report, True)

    if prediction.get("match_openness") not in OPENNESS_VALUES | {None}:
        report.errors.append(f"{path}.prediction.match_openness: 非法枚举")
    if prediction.get("direction_confidence") not in CONFIDENCE_VALUES | {None}:
        report.errors.append(f"{path}.prediction.direction_confidence: 非法枚举")
    if prediction.get("risk_level") not in RISK_VALUES | {None}:
        report.errors.append(f"{path}.prediction.risk_level: 非法枚举")
    if mode == "formal" and prediction.get("match_openness") not in OPENNESS_VALUES:
        report.errors.append(f"{path}.prediction.match_openness: formal记录必须填写")
    if mode == "formal" and prediction.get("risk_level") not in RISK_VALUES:
        report.errors.append(f"{path}.prediction.risk_level: formal记录必须填写")
    if mode == "formal" and result is not None and prediction.get("direction_confidence") not in CONFIDENCE_VALUES:
        report.errors.append(f"{path}.prediction.direction_confidence: 有正式胜平负选项时必须填写")

    comparison = prediction.get("rule_application_comparison")
    if not isinstance(comparison, dict):
        report.errors.append(f"{path}.prediction.rule_application_comparison: 缺少对象")
    else:
        before = comparison.get("before") if isinstance(comparison.get("before"), dict) else {}
        after = comparison.get("after") if isinstance(comparison.get("after"), dict) else {}
        _validate_before_source(before, f"{path}.prediction.rule_application_comparison.before", base_dir, report, mode)
        before_time = _validate_pre_match_time(before.get("saved_at"), kickoff, f"{path}.prediction.rule_application_comparison.before.saved_at", report, mode == "formal")
        after_time = _validate_pre_match_time(after.get("saved_at"), kickoff, f"{path}.prediction.rule_application_comparison.after.saved_at", report, mode == "formal")
        if before_time and after_time and before_time > after_time:
            report.errors.append(f"{path}.prediction.rule_application_comparison: before必须早于或等于after")
        finalized = _parse_time(prediction_set.get("finalized_at"))
        if after_time and finalized and after_time > finalized:
            report.errors.append(f"{path}.prediction.rule_application_comparison: after不得晚于finalized_at")
        if mode == "formal" and comparison.get("created_pre_match") is not True:
            report.errors.append(f"{path}.prediction.rule_application_comparison.created_pre_match: formal记录必须为true")
        if comparison.get("no_post_match_backfill") is not True:
            report.errors.append(f"{path}.prediction.rule_application_comparison.no_post_match_backfill: 必须为true")

    if mode == "formal":
        if not official_data.get("checked_at"):
            report.errors.append(f"{path}.official_data.checked_at: formal记录必须保存来源核对时间")
        for source_path, value in _iter_source_times({"pre_match_facts": match.get("pre_match_facts", {}), "official_data": official_data, "prediction": prediction}):
            if value is not None:
                _validate_pre_match_time(value, kickoff, f"{path}.{source_path}", report, False)


def validate_document(data: Any, *, mode: str = "formal", base_dir: Path | None = None) -> ValidationReport:
    """Validate a v4 document without modifying it."""
    if mode not in {"formal", "draft"}:
        raise ValueError("mode must be 'formal' or 'draft'")
    report = ValidationReport()
    if not isinstance(data, dict):
        report.errors.append("根对象必须是JSON对象")
        return report
    if data.get("schema_version") != SCHEMA_VERSION:
        report.skipped.append(f"schema_version={data.get('schema_version')!r}，不是v4；未按v4判定合格")
        return report
    if isinstance(data.get("prediction_sets"), list):
        sets = data["prediction_sets"]
    elif isinstance(data.get("matches"), list):
        sets = [data]
    else:
        report.errors.append("v4文档必须含prediction_sets或matches")
        return report

    set_keys: set[tuple[Any, Any]] = set()
    for set_index, prediction_set in enumerate(sets):
        path = f"prediction_sets[{set_index}]"
        if not isinstance(prediction_set, dict):
            report.errors.append(f"{path}: 必须是对象")
            continue
        if prediction_set.get("schema_version") != SCHEMA_VERSION:
            report.errors.append(f"{path}.schema_version: 必须为4")
        key = (prediction_set.get("prediction_date"), prediction_set.get("prediction_version"))
        if key in set_keys:
            report.errors.append(f"{path}: 日期与版本重复 {key}")
        set_keys.add(key)
        if prediction_set.get("state") != ("formal" if mode == "formal" else "draft"):
            report.errors.append(f"{path}.state: {mode}模式要求{'formal' if mode == 'formal' else 'draft'}")
        if set(prediction_set.get("prediction_scope", [])) != set(PLAYS):
            report.errors.append(f"{path}.prediction_scope: v4默认范围必须为result和handicap_result")

        matches = prediction_set.get("matches")
        if not isinstance(matches, list) or not matches:
            report.errors.append(f"{path}.matches: 必须是非空列表")
            continue
        numbers = [match.get("match_number") for match in matches if isinstance(match, dict)]
        sequences = [match.get("sequence") for match in matches if isinstance(match, dict)]
        if len(numbers) != len(set(numbers)):
            report.errors.append(f"{path}.matches: 比赛编号重复")
        if len(sequences) != len(set(sequences)):
            report.errors.append(f"{path}.matches: sequence重复")
        if sequences != list(range(1, len(matches) + 1)):
            report.errors.append(f"{path}.matches: sequence必须按用户顺序连续为1..N")

        kickoffs = [_parse_time(match.get("kickoff_time")) for match in matches if isinstance(match, dict)]
        first_kickoff = min((time for time in kickoffs if time is not None), default=None)
        if mode == "formal":
            generated = _validate_pre_match_time(prediction_set.get("generated_at"), first_kickoff, f"{path}.generated_at", report, True)
            finalized = _validate_pre_match_time(prediction_set.get("finalized_at"), first_kickoff, f"{path}.finalized_at", report, True)
            freeze = prediction_set.get("freeze") if isinstance(prediction_set.get("freeze"), dict) else {}
            frozen = _validate_pre_match_time(freeze.get("frozen_at"), first_kickoff, f"{path}.freeze.frozen_at", report, True)
            if freeze.get("is_frozen") is not True or not freeze.get("frozen_by") or not freeze.get("freeze_reason"):
                report.errors.append(f"{path}.freeze: formal记录必须有完整冻结元信息")
            if not isinstance(prediction_set.get("snapshot_hash"), str) or not HASH_RE.fullmatch(prediction_set["snapshot_hash"]):
                report.errors.append(f"{path}.snapshot_hash: formal记录必须有64位SHA-256")
            if generated and finalized and generated > finalized:
                report.errors.append(f"{path}: generated_at不得晚于finalized_at")
            if finalized and frozen and finalized > frozen:
                report.errors.append(f"{path}: finalized_at不得晚于frozen_at")
            version = prediction_set.get("prediction_version")
            if not _is_int(version) or version < 1:
                report.errors.append(f"{path}.prediction_version: 必须是正整数")
            elif version == 1 and prediction_set.get("parent_version") is not None:
                report.errors.append(f"{path}.parent_version: V1必须为null")
            elif version > 1:
                if prediction_set.get("parent_version") != version - 1:
                    report.errors.append(f"{path}.parent_version: 新版本必须引用上一版本")
                amendment = prediction_set.get("amendment") if isinstance(prediction_set.get("amendment"), dict) else {}
                if amendment.get("version_type") not in {"事实更新", "录入纠错", "重新评估"}:
                    report.errors.append(f"{path}.amendment.version_type: 非法或缺失")
                for key_name in ("reason_category", "reason_detail", "evidence_source", "changed_at"):
                    if not amendment.get(key_name):
                        report.errors.append(f"{path}.amendment.{key_name}: 新版本必填")
            timeline = prediction_set.get("rule_application_timeline") if isinstance(prediction_set.get("rule_application_timeline"), dict) else {}
            if timeline.get("no_post_match_backfill") is not True:
                report.errors.append(f"{path}.rule_application_timeline.no_post_match_backfill: 必须为true")
            timeline_times = {
                key_name: _validate_pre_match_time(timeline.get(key_name), first_kickoff, f"{path}.rule_application_timeline.{key_name}", report, True)
                for key_name in ("independent_judgment_saved_at", "rules_read_at", "rules_applied_at", "comparison_saved_at")
            }
            independent = timeline_times["independent_judgment_saved_at"]
            rules_read = timeline_times["rules_read_at"]
            rules_applied = timeline_times["rules_applied_at"]
            comparison_saved = timeline_times["comparison_saved_at"]
            if independent and rules_applied and independent > rules_applied:
                report.errors.append(f"{path}.rule_application_timeline: independent_judgment_saved_at不得晚于rules_applied_at")
            if rules_read and rules_applied and rules_read > rules_applied:
                report.errors.append(f"{path}.rule_application_timeline: rules_read_at不得晚于rules_applied_at")
            if rules_applied and comparison_saved and rules_applied > comparison_saved:
                report.errors.append(f"{path}.rule_application_timeline: rules_applied_at不得晚于comparison_saved_at")
            if comparison_saved and finalized and comparison_saved > finalized:
                report.errors.append(f"{path}.rule_application_timeline: comparison_saved_at不得晚于finalized_at")

        for index, match in enumerate(matches):
            if isinstance(match, dict):
                _validate_match(match, index, prediction_set, mode, base_dir, report)
            else:
                report.errors.append(f"{path}.matches[{index}]: 必须是对象")
    return report


def _canonical_record(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def select_effective_versions(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the highest formal pre-kickoff version per date/match; never fill plays across versions."""
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("state") != "formal":
            continue
        finalized = _parse_time(record.get("finalized_at"))
        kickoff = _parse_time(record.get("kickoff_time"))
        version = record.get("prediction_version")
        if finalized is None or kickoff is None or finalized >= kickoff or not _is_int(version) or version < 1:
            continue
        grouped.setdefault((record.get("prediction_date"), record.get("match_number")), []).append(record)

    selected: list[dict[str, Any]] = []
    for key, candidates in grouped.items():
        by_version: dict[int, list[dict[str, Any]]] = {}
        for record in candidates:
            by_version.setdefault(record["prediction_version"], []).append(record)
        for version, same_version_records in by_version.items():
            fingerprints = {_canonical_record(record) for record in same_version_records}
            if len(fingerprints) > 1:
                raise VersionConflictError(f"{key}: formal版本V{version}存在不同记录")
        highest = max(record["prediction_version"] for record in candidates)
        same_version = [record for record in candidates if record["prediction_version"] == highest]
        selected.append(same_version[0])
    return sorted(selected, key=lambda record: (record.get("prediction_date", ""), record.get("sequence", 0), record.get("match_number", "")))


def statistics_eligibility(record: dict[str, Any], play: str) -> tuple[bool, str]:
    """Return whether a frozen play enters current hit-rate statistics."""
    if play not in {"result", "handicap_result", "score", "total_goals"}:
        raise ValueError(f"unknown play: {play}")
    final = record.get("final_result") if isinstance(record.get("final_result"), dict) else {}
    completion = final.get("match_completion_status", record.get("match_status"))
    if completion in NON_FINAL_MATCH_STATUSES:
        return False, f"比赛状态为{completion}"
    if final.get("verified_90_minutes") is not True:
        return False, "90分钟赛果未确认"

    prediction = record.get("prediction") if isinstance(record.get("prediction"), dict) else record
    explicit_plays = prediction.get("prediction_plays", record.get("prediction_plays"))
    field_names = {
        "result": "predicted_result",
        "handicap_result": "predicted_handicap_result",
        "score": "predicted_score",
        "total_goals": "predicted_total_goals",
    }
    value = prediction.get(field_names[play])
    if isinstance(explicit_plays, list):
        if play not in explicit_plays:
            return False, "该冻结版本未预测此玩法"
    elif value is None:
        return False, "历史记录无此玩法预测值"
    if value is None:
        return False, "该冻结版本无唯一正式选项"

    play_status = prediction.get("play_status")
    if isinstance(play_status, dict) and isinstance(play_status.get(play), dict):
        status = play_status[play].get("status")
        if status != "已确认":
            return False, f"玩法状态为{status}"
    return True, "90分钟赛果已确认且冻结版本含该玩法"


def _main() -> int:
    parser = argparse.ArgumentParser(description="只读校验足球预测schema v4")
    parser.add_argument("path", type=Path)
    parser.add_argument("--mode", choices=("formal", "draft"), default="formal")
    args = parser.parse_args()
    with args.path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    report = validate_document(data, mode=args.mode, base_dir=args.path.resolve().parent)
    if report.skipped:
        print("SKIP")
        for message in report.skipped:
            print(f"- {message}")
        return 0
    if report.errors:
        print(f"FAIL ({len(report.errors)} errors)")
        for message in report.errors:
            print(f"- {message}")
        return 1
    print("PASS")
    for message in report.warnings:
        print(f"- warning: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
