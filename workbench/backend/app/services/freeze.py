from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .history import atomic_json_write, sha256_file


class FreezeError(RuntimeError):
    pass


_PROCESS_LOCK = threading.Lock()


def _load_validator(project_root: Path):
    path = project_root / "scripts" / "validate_prediction.py"
    spec = importlib.util.spec_from_file_location("project_prediction_validator", path)
    if spec is None or spec.loader is None:
        raise FreezeError("无法加载项目formal校验器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise FreezeError("时间必须带时区")
    return parsed


def _flatten_set(prediction_set: dict[str, Any], source_file: str) -> list[dict[str, Any]]:
    common = {key: value for key, value in prediction_set.items() if key != "matches"}
    return [{**common, **match, "source_prediction_file": source_file} for match in prediction_set["matches"]]


class FreezeService:
    def __init__(self, project_root: Path, history_path: Path, backup_dir: Path) -> None:
        self.project_root = project_root
        self.history_path = history_path
        self.backup_dir = backup_dir

    def commit(
        self,
        document: dict[str, Any],
        *,
        source_path: Path,
        idempotency_key: str,
        expected_history_sha256: str,
        enabled: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not enabled:
            raise FreezeError("真实formal写入未启用；设置SPORTTERY_ENABLE_FORMAL_WRITE=1后仍需明确冻结动作")
        validator = _load_validator(self.project_root)
        report = validator.validate_document(document, mode="formal", base_dir=source_path.resolve().parent)
        if report.errors or report.skipped:
            raise FreezeError("formal校验失败: " + "; ".join(report.errors or report.skipped))
        sets = document.get("prediction_sets") or [document]
        current_time = now or datetime.now(timezone.utc)
        for prediction_set in sets:
            for match in prediction_set["matches"]:
                if current_time >= _parse_time(match["kickoff_time"]):
                    raise FreezeError(f"{match.get('match_number')}: 已到开赛时间")
        with _PROCESS_LOCK:
            current_hash = sha256_file(self.history_path)
            if current_hash != expected_history_sha256:
                raise FreezeError("权威历史已变化，父版本冲突")
            history = json.loads(self.history_path.read_text(encoding="utf-8-sig"))
            receipts = history.setdefault("workbench_idempotency", {})
            if idempotency_key in receipts:
                return {"status": "idempotent", **receipts[idempotency_key]}
            existing = {
                (r.get("prediction_date"), r.get("match_number"), r.get("prediction_version"))
                for r in history.get("records", [])
            }
            additions: list[dict[str, Any]] = []
            for prediction_set in sets:
                for record in _flatten_set(prediction_set, str(source_path)):
                    key = (record.get("prediction_date"), record.get("match_number"), record.get("prediction_version"))
                    if key in existing:
                        raise FreezeError(f"同日同编号同版本已存在: {key}")
                    additions.append(record)
            second_now = datetime.now(timezone.utc) if now is None else now
            for record in additions:
                if second_now >= _parse_time(record["kickoff_time"]):
                    raise FreezeError(f"{record.get('match_number')}: 提交期间跨过开赛点")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            backup = self.backup_dir / f"prediction_history_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
            shutil.copy2(self.history_path, backup)
            history.setdefault("records", []).extend(additions)
            history["record_count"] = len(history["records"])
            history["updated_at"] = datetime.now(timezone.utc).isoformat()
            receipt = {
                "status": "committed",
                "records_added": len(additions),
                "backup": str(backup),
                "committed_at": datetime.now(timezone.utc).isoformat(),
            }
            receipts[idempotency_key] = receipt
            atomic_json_write(self.history_path, history)
            receipt["history_sha256_after"] = sha256_file(self.history_path)
            return receipt
