#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from validate_prediction import (
    VersionConflictError,
    select_effective_versions,
    statistics_eligibility,
    validate_document,
)


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


class TemplateTests(unittest.TestCase):
    def test_pre_match_template_is_deep_copy_of_v4_prediction_set(self):
        full = load_json(ROOT / "data_structure_template.json")
        pre = load_json(ROOT / "templates" / "赛前预测模板.json")
        self.assertEqual(full["schema_version"], 4)
        self.assertEqual(full["prediction_sets"][0], pre)

    def test_draft_template_passes_draft_mode(self):
        pre = load_json(ROOT / "templates" / "赛前预测模板.json")
        report = validate_document(pre, mode="draft", base_dir=ROOT)
        self.assertEqual(report.errors, [])
        self.assertFalse(report.skipped)

    def test_old_schema_is_skipped_not_reported_as_v4_pass(self):
        report = validate_document({"schema_version": 3, "records": []})
        self.assertTrue(report.skipped)
        self.assertFalse(report.is_valid)


class FormalValidationTests(unittest.TestCase):
    def setUp(self):
        self.base = ROOT / "scripts"
        self.source = Path(__file__).resolve()
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def make_formal(self, handicap=-1, result="胜", handicap_result="让平"):
        data = load_json(ROOT / "templates" / "赛前预测模板.json")
        data.update(
            {
                "prediction_date": "2026-09-17",
                "state": "formal",
                "generated_at": "2026-09-17T08:00:00+08:00",
                "finalized_at": "2026-09-17T09:00:00+08:00",
                "snapshot_hash": "a" * 64,
            }
        )
        data["freeze"] = {
            "is_frozen": True,
            "frozen_at": "2026-09-17T09:00:00+08:00",
            "frozen_by": "prediction_workflow",
            "freeze_reason": "校验通过后冻结",
        }
        data["rule_application_timeline"] = {
            "independent_judgment_file": "test_validate_prediction.py",
            "independent_judgment_sha256": self.source_hash,
            "independent_judgment_saved_at": "2026-09-17T08:10:00+08:00",
            "rules_read_at": "2026-09-17T08:11:00+08:00",
            "rules_applied_at": "2026-09-17T08:12:00+08:00",
            "comparison_saved_at": "2026-09-17T08:13:00+08:00",
            "no_post_match_backfill": True,
        }
        match = data["matches"][0]
        match.update(
            {
                "match_number": "周四001",
                "match_status": "未开赛",
                "kickoff_time": "2026-09-17T12:00:00+08:00",
            }
        )
        match["official_data"].update(
            {
                "source_type": "中国体彩官方页面",
                "source_url": "https://example.invalid/official",
                "checked_at": "2026-09-17T08:20:00+08:00",
                "sale_status": "已开售",
            }
        )
        match["official_data"]["spf"]["status"] = "已确认"
        match["official_data"]["handicap"].update({"status": "已确认", "official_handicap": handicap})
        prediction = match["prediction"]
        prediction.update(
            {
                "predicted_result": result,
                "predicted_handicap_result": handicap_result,
                "prediction_reason": "测试独立判断",
                "match_openness": "中",
                "direction_confidence": "中",
                "risk_level": "中",
                "prediction_plays": ["result", "handicap_result"],
            }
        )
        prediction["play_status"] = {
            "result": {"status": "已确认", "selection": result, "confidence": "中", "reason_unavailable": None},
            "handicap_result": {"status": "已确认", "selection": handicap_result, "confidence": "中", "reason_unavailable": None},
        }
        prediction["three_way_probabilities"] = {
            "home": 50,
            "draw": 30,
            "away": 20,
            "method": "证据约束主观估计",
            "reason_unavailable": None,
            "evidence_checked_at": "2026-09-17T08:25:00+08:00",
            "estimated_at": "2026-09-17T08:30:00+08:00",
        }
        prediction["handicap_assessment"].update(
            {
                "status": "已完成",
                "official_handicap": handicap,
                "most_likely_region": "d+h=0",
                "assessed_at": "2026-09-17T08:31:00+08:00",
            }
        )
        comparison = prediction["rule_application_comparison"]
        comparison["before"].update(
            {
                "source_file": "test_validate_prediction.py",
                "source_sha256": self.source_hash,
                "saved_at": "2026-09-17T08:10:00+08:00",
                "result": result,
                "handicap_result": handicap_result,
            }
        )
        comparison["after"].update(
            {
                "saved_at": "2026-09-17T08:13:00+08:00",
                "result": result,
                "handicap_result": handicap_result,
            }
        )
        comparison["created_pre_match"] = True
        return data

    def assert_valid(self, data):
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertEqual(report.errors, [], "\n".join(report.errors))

    def test_handicap_logic_plus_one_minus_one_minus_two_and_zero(self):
        cases = [
            (+1, "负", "让平"),
            (-1, "胜", "让平"),
            (-2, "胜", "让平"),
            (0, "平", "让平"),
        ]
        for handicap, result, handicap_result in cases:
            with self.subTest(handicap=handicap):
                self.assert_valid(self.make_formal(handicap, result, handicap_result))

    def test_incompatible_two_play_pair_is_rejected(self):
        data = self.make_formal(-1, "负", "让胜")
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("共同满足" in error for error in report.errors))

    def test_unknown_handicap_cannot_have_handicap_selection(self):
        data = self.make_formal()
        data["matches"][0]["official_data"]["handicap"]["official_handicap"] = None
        data["matches"][0]["prediction"]["handicap_assessment"]["official_handicap"] = None
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("未知或非整数让球" in error for error in report.errors))

    def test_unoffered_play_has_status_but_no_formal_selection(self):
        data = self.make_formal(-1, "胜", "让平")
        match = data["matches"][0]
        prediction = match["prediction"]
        prediction["predicted_result"] = None
        prediction["prediction_plays"] = ["handicap_result"]
        prediction["play_status"]["result"] = {
            "status": "未开售",
            "selection": None,
            "confidence": None,
            "reason_unavailable": "中国体彩未开售普通胜平负",
        }
        match["official_data"]["spf"]["status"] = "未开售"
        prediction["rule_application_comparison"]["before"]["result"] = None
        prediction["rule_application_comparison"]["after"]["result"] = None
        self.assert_valid(data)

    def test_waiting_sale_blocks_but_paused_sale_does_not_erase_analysis(self):
        data = self.make_formal()
        data["matches"][0]["official_data"]["spf"]["status"] = "官方待开售"
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("未开售玩法不得有正式选项" in error for error in report.errors))
        data["matches"][0]["official_data"]["spf"]["status"] = "暂停销售，官方玩法已存在"
        self.assert_valid(data)

    def test_probability_requires_sum_or_reason(self):
        data = self.make_formal()
        data["matches"][0]["prediction"]["three_way_probabilities"]["away"] = 19
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("合计必须为100" in error for error in report.errors))
        probabilities = data["matches"][0]["prediction"]["three_way_probabilities"]
        probabilities.update({"home": None, "draw": None, "away": None, "reason_unavailable": "证据不足"})
        self.assert_valid(data)

    def test_before_file_hash_is_verified(self):
        data = self.make_formal()
        data["matches"][0]["prediction"]["rule_application_comparison"]["before"]["source_sha256"] = "0" * 64
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("与引用文件不一致" in error for error in report.errors))

    def test_invalid_non_null_source_time_and_timeline_order_are_rejected(self):
        data = self.make_formal()
        data["matches"][0]["official_data"]["checked_at"] = "not-a-time"
        data["rule_application_timeline"]["comparison_saved_at"] = "2026-09-17T09:01:00+08:00"
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("official_data.checked_at" in error for error in report.errors))
        self.assertTrue(any("comparison_saved_at不得晚于finalized_at" in error for error in report.errors))

    def test_score_aliases_and_missing_risk_fields_are_rejected(self):
        data = self.make_formal()
        prediction = data["matches"][0]["prediction"]
        prediction["score"] = "2:1"
        prediction["risk_level"] = None
        prediction["match_openness"] = None
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("禁止保存比分" in error for error in report.errors))
        self.assertTrue(any("risk_level: formal记录必须填写" in error for error in report.errors))
        self.assertTrue(any("match_openness: formal记录必须填写" in error for error in report.errors))

    def test_duplicate_number_and_sequence_are_rejected(self):
        data = self.make_formal()
        data["matches"].append(copy.deepcopy(data["matches"][0]))
        report = validate_document(data, mode="formal", base_dir=self.base)
        self.assertTrue(any("比赛编号重复" in error for error in report.errors))
        self.assertTrue(any("sequence重复" in error for error in report.errors))


class SelectionAndStatisticsTests(unittest.TestCase):
    @staticmethod
    def record(version, finalized, *, plays=None, state="formal"):
        return {
            "prediction_date": "2026-09-17",
            "match_number": "周四001",
            "sequence": 1,
            "prediction_version": version,
            "state": state,
            "finalized_at": finalized,
            "kickoff_time": "2026-09-17T12:00:00+08:00",
            "prediction": {
                "prediction_plays": ["result", "handicap_result"] if plays is None else plays,
                "predicted_result": "胜" if plays is None or "result" in plays else None,
                "predicted_handicap_result": "让平" if plays is None or "handicap_result" in plays else None,
            },
        }

    def test_highest_pre_kickoff_formal_version_and_no_play_fill(self):
        records = [
            self.record(1, "2026-09-17T08:00:00+08:00"),
            self.record(2, "2026-09-17T09:00:00+08:00", plays=["handicap_result"]),
            self.record(3, "2026-09-17T12:01:00+08:00"),
            self.record(4, "2026-09-17T10:00:00+08:00", state="draft"),
        ]
        selected = select_effective_versions(records)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["prediction_version"], 2)
        self.assertEqual(selected[0]["prediction"]["prediction_plays"], ["handicap_result"])
        self.assertIsNone(selected[0]["prediction"]["predicted_result"])

    def test_same_highest_version_conflict_raises(self):
        first = self.record(2, "2026-09-17T09:00:00+08:00")
        second = copy.deepcopy(first)
        second["prediction"]["predicted_result"] = "平"
        with self.assertRaises(VersionConflictError):
            select_effective_versions([first, second])

    def test_conflict_in_lower_version_also_raises(self):
        first = self.record(1, "2026-09-17T08:00:00+08:00")
        conflicting = copy.deepcopy(first)
        conflicting["prediction"]["predicted_result"] = "平"
        highest = self.record(2, "2026-09-17T09:00:00+08:00")
        with self.assertRaises(VersionConflictError):
            select_effective_versions([first, conflicting, highest])

    def test_postponed_and_unoffered_are_excluded(self):
        record = self.record(1, "2026-09-17T09:00:00+08:00")
        record["final_result"] = {"match_completion_status": "延期", "verified_90_minutes": False}
        self.assertFalse(statistics_eligibility(record, "result")[0])

        record["final_result"] = {"match_completion_status": "已完赛", "verified_90_minutes": True}
        record["prediction"]["prediction_plays"] = ["handicap_result"]
        record["prediction"]["predicted_result"] = None
        record["prediction"]["play_status"] = {
            "result": {"status": "未开售"},
            "handicap_result": {"status": "已确认"},
        }
        self.assertFalse(statistics_eligibility(record, "result")[0])
        self.assertTrue(statistics_eligibility(record, "handicap_result")[0])

    def test_external_90_minute_fact_can_count_without_official_settlement(self):
        record = self.record(1, "2026-09-17T09:00:00+08:00")
        record["final_result"] = {
            "match_completion_status": "已完赛",
            "verified_90_minutes": True,
            "fact_confirmation": {"status": "已确认"},
            "official_settlement": {"status": "待核实"},
        }
        self.assertTrue(statistics_eligibility(record, "result")[0])

    def test_historical_four_play_record_remains_compatible(self):
        legacy = {
            "prediction": {
                "predicted_result": "胜",
                "predicted_handicap_result": "让平",
                "predicted_score": "2:1",
                "predicted_total_goals": 3,
            },
            "final_result": {"verified_90_minutes": True},
        }
        for play in ("result", "handicap_result", "score", "total_goals"):
            self.assertTrue(statistics_eligibility(legacy, play)[0], play)


if __name__ == "__main__":
    unittest.main(verbosity=2)
