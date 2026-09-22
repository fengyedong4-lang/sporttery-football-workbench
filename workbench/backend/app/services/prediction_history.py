"""Read-only discovery of every saved prediction representation.

This module deliberately does not use the review/version selector.  The history
browser is an archive view: old schemas, superseded versions and duplicate rows
must remain visible exactly as saved.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal


CHINA = timezone(timedelta(hours=8))
SAFE_DRAFT_ID = re.compile(r"[0-9a-f]{32}")
SAFE_OPAQUE_ID = re.compile(r"[0-9a-f]{64}")
HistoryKind = Literal["all", "draft", "formal", "export"]
DetailKind = Literal["draft", "formal", "export"]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _opaque_id(kind: str, *parts: Any) -> str:
    payload = _canonical([kind, *parts]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA)
    return parsed


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _beijing_date(value: Any) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.astimezone(CHINA).date() if parsed else None


def _latest_time(rows: list[Any]) -> str | None:
    candidates: list[tuple[datetime, str]] = []
    for raw in rows:
        row = _mapping(raw)
        for field in ("finalized_at", "generated_at", "created_at"):
            value = row.get(field)
            parsed = _parse_datetime(value)
            if parsed:
                candidates.append((parsed.astimezone(timezone.utc), str(value)))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def _field(row: Any, *names: str) -> Any:
    item = _mapping(row)
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return None


def _competitions(matches: list[Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for row in matches:
        value = _field(row, "competition", "league", "league_name", "leagueNameAbbr")
        if value in (None, ""):
            continue
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            found.append(text)
    return found or ["未知赛事"]


def _search_text(label: str, matches: list[Any]) -> str:
    values = [label]
    names = (
        "match_number", "match_no", "matchNumStr", "match_id", "matchId",
        "competition", "league", "league_name", "leagueNameAbbr",
        "home_team", "homeTeam", "away_team", "awayTeam",
    )
    for row in matches:
        item = _mapping(row)
        values.extend(str(item[name]) for name in names if item.get(name) not in (None, ""))
    return "\n".join(values).casefold()


def _status(kind: DetailKind, raw: Any, matches: list[Any]) -> str:
    container = _mapping(raw)
    direct = container.get("status") or container.get("state") or container.get("completion_status")
    if direct not in (None, ""):
        return str(direct)
    states = {
        str(value).strip()
        for row in matches
        for value in [_field(row, "status", "state", "match_status")]
        if value not in (None, "")
    }
    if len(states) == 1:
        return states.pop()
    if len(states) > 1:
        return "多状态"
    return {"draft": "已保存草稿", "formal": "历史记录（状态未知）", "export": "赛前原始存档"}[kind]


def _display_version(value: Any) -> str:
    if value in (None, ""):
        return "V未知"
    if isinstance(value, (str, int, float, bool)):
        return f"V{value}"
    return "V无法映射"


@dataclass(frozen=True)
class PredictionBatch:
    id: str
    kind: DetailKind
    label: str
    created_at: str | None
    prediction_date: str | None
    matches: list[Any]
    raw: Any
    status: str
    competitions: list[str]
    search_text: str
    source_path: str | None = None
    source_set_index: int | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "created_at": self.created_at,
            "prediction_date": self.prediction_date,
            "match_count": len(self.matches),
            "competitions": self.competitions,
            "status": self.status,
        }

    def detail(self) -> dict[str, Any]:
        result = {
            **self.summary(),
            "matches": self.matches,
            "raw": self.raw,
            "read_only": True,
        }
        if self.source_path is not None:
            result["source_path"] = self.source_path
        if self.source_set_index is not None:
            result["source_set_index"] = self.source_set_index
        return result

    @property
    def filter_dates(self) -> set[date]:
        values: set[date] = set()
        predicted = _parse_date(self.prediction_date)
        generated = _beijing_date(self.created_at)
        if predicted:
            values.add(predicted)
        if generated:
            values.add(generated)
        return values


@dataclass(frozen=True)
class PredictionCorpus:
    batches: list[PredictionBatch]
    warnings: list[str]
    counts: dict[str, int]


def _new_batch(
    *,
    batch_id: str,
    kind: DetailKind,
    label: str,
    created_at: str | None,
    prediction_date: str | None,
    matches: list[Any],
    raw: Any,
    source_path: str | None = None,
    source_set_index: int | None = None,
    status_source: Any | None = None,
) -> PredictionBatch:
    competitions = _competitions(matches)
    return PredictionBatch(
        id=batch_id,
        kind=kind,
        label=label,
        created_at=created_at,
        prediction_date=prediction_date,
        matches=matches,
        raw=raw,
        status=_status(kind, raw if status_source is None else status_source, matches),
        competitions=competitions,
        search_text=_search_text(label, matches),
        source_path=source_path,
        source_set_index=source_set_index,
    )


def _draft_batches(runtime_dir: Path, warnings: list[str]) -> list[PredictionBatch]:
    drafts_dir = runtime_dir / "drafts"
    batches: list[PredictionBatch] = []
    if not drafts_dir.is_dir():
        return batches
    for path in sorted(drafts_dir.glob("*.json"), key=lambda item: item.name):
        if path.is_symlink():
            warnings.append(f"草稿 {path.name} 是符号链接，已跳过")
            continue
        if not SAFE_DRAFT_ID.fullmatch(path.stem):
            warnings.append(f"草稿 {path.name} 文件名不是安全批次编号，已跳过")
            continue
        try:
            raw = _read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            warnings.append(f"草稿 {path.name} 无法读取：{type(exc).__name__}")
            continue
        document = _mapping(raw)
        if document.get("run_id") != path.stem or not isinstance(document.get("matches"), list):
            warnings.append(f"草稿 {path.name} 结构或 run_id 无效，已跳过")
            continue
        matches = document["matches"]
        # A draft's created_at is the run start; completed_at is when the saved
        # result became available. Prefer it for the archive's save-time label.
        created_at = document.get("completed_at") or document.get("created_at")
        explicit_date = _parse_date(document.get("prediction_date"))
        prediction_day = explicit_date or _beijing_date(created_at)
        label_day = prediction_day.isoformat() if prediction_day else "未知日期"
        label = f"预测草稿 {label_day} · {path.stem[:8]}"
        batches.append(_new_batch(
            batch_id=path.stem,
            kind="draft",
            label=label,
            created_at=str(created_at) if created_at not in (None, "") else None,
            prediction_date=prediction_day.isoformat() if prediction_day else None,
            matches=matches,
            raw=raw,
        ))
    return batches


def _formal_batches(history_path: Path, warnings: list[str]) -> tuple[list[PredictionBatch], int]:
    try:
        raw = _read_json(history_path)
    except FileNotFoundError:
        warnings.append("正式历史文件不存在")
        return [], 0
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(f"正式历史文件无法读取：{type(exc).__name__}")
        return [], 0
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
        warnings.append("正式历史文件 records 结构无效")
        return [], 0
    records = raw["records"]
    grouped: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    for offset, record in enumerate(records):
        row = _mapping(record)
        if not isinstance(record, dict):
            warnings.append(f"正式历史第 {offset + 1} 条不是对象，已保留在未知批次")
        raw_date = row.get("prediction_date")
        raw_version = row.get("prediction_version")
        key = (_canonical(raw_date), _canonical(raw_version))
        group = grouped.setdefault(key, {
            "prediction_date": raw_date,
            "prediction_version": raw_version,
            "records": [],
        })
        group["records"].append(record)

    batches: list[PredictionBatch] = []
    for group in grouped.values():
        rows = group["records"]
        raw_date = group["prediction_date"]
        raw_version = group["prediction_version"]
        parsed_date = _parse_date(raw_date)
        prediction_day = parsed_date.isoformat() if parsed_date else None
        date_label = prediction_day or ("未知日期" if raw_date in (None, "") else "日期无法映射")
        version_label = _display_version(raw_version)
        batch_id = _opaque_id("formal", raw_date, raw_version)
        label = f"正式历史 {date_label} · {version_label}"
        created_at = _latest_time(rows)
        batches.append(_new_batch(
            batch_id=batch_id,
            kind="formal",
            label=label,
            created_at=created_at,
            prediction_date=prediction_day,
            matches=rows,
            raw={
                "prediction_date": raw_date,
                "prediction_version": raw_version,
                "records": rows,
            },
        ))
    return batches, len(records)


def _is_backup_relative(path: Path) -> bool:
    for part in path.parent.parts:
        lowered = part.casefold()
        if "backup" in lowered or "备份" in part or "修改前" in part:
            return True
    return False


def _extract_matches(container: Any) -> tuple[list[Any], str | None]:
    item = _mapping(container)
    if isinstance(item.get("matches"), list):
        return item["matches"], "matches"
    if isinstance(item.get("records"), list):
        return item["records"], "records"
    return [], None


def _export_batches(project_root: Path, warnings: list[str]) -> tuple[list[PredictionBatch], int]:
    exports_dir = project_root / "exports"
    if not exports_dir.is_dir():
        return [], 0
    batches: list[PredictionBatch] = []
    record_count = 0
    candidates = sorted(exports_dir.rglob("prediction_V*.json"), key=lambda item: item.as_posix())
    for path in candidates:
        relative_to_exports = path.relative_to(exports_dir)
        if _is_backup_relative(relative_to_exports):
            continue
        relative = path.relative_to(project_root).as_posix()
        if path.is_symlink():
            warnings.append(f"赛前存档 {relative} 是符号链接，已跳过")
            continue
        try:
            document = _read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            warnings.append(f"赛前存档 {relative} 无法读取：{type(exc).__name__}")
            continue
        if not isinstance(document, dict):
            warnings.append(f"赛前存档 {relative} 顶层不是对象，已跳过")
            continue

        groups: list[tuple[str, Any, dict[str, Any], int | None]] = []
        _, root_field = _extract_matches(document)
        prediction_sets = document.get("prediction_sets")
        if root_field is not None:
            groups.append(("root", document, document, None))
        if isinstance(prediction_sets, list):
            for index, prediction_set in enumerate(prediction_sets):
                groups.append((f"prediction_sets:{index}", prediction_set, document, index))
        elif prediction_sets is not None:
            groups.append(("prediction_sets:0", prediction_sets, document, 0))
        if not groups:
            groups.append(("root", document, document, None))

        for token, selected, envelope, set_index in groups:
            matches, source_field = _extract_matches(selected)
            if source_field is None:
                warnings.append(f"赛前存档 {relative} 的 {token} 未找到 matches/records 数组，按空批次保留")
            selected_map = _mapping(selected)
            raw_date = selected_map.get("prediction_date", envelope.get("prediction_date"))
            raw_version = selected_map.get("prediction_version", envelope.get("prediction_version"))
            created_at = (
                selected_map.get("generated_at")
                or selected_map.get("finalized_at")
                or envelope.get("generated_at")
                or envelope.get("finalized_at")
                or envelope.get("created_at")
            )
            parsed_date = _parse_date(raw_date) or _beijing_date(created_at)
            prediction_day = parsed_date.isoformat() if parsed_date else None
            date_label = prediction_day or ("未知日期" if raw_date in (None, "") else "日期无法映射")
            version_label = _display_version(raw_version)
            suffix = "" if token == "root" else f" · 集合{int(token.rsplit(':', 1)[1]) + 1}"
            label = f"赛前原始存档 {date_label} · {version_label}{suffix} · {relative}"
            batch_id = _opaque_id("export", relative, token)
            batches.append(_new_batch(
                batch_id=batch_id,
                kind="export",
                label=label,
                created_at=str(created_at) if created_at not in (None, "") else None,
                prediction_date=prediction_day,
                matches=matches,
                raw=document,
                source_path=relative,
                source_set_index=set_index,
                status_source=selected,
            ))
            record_count += len(matches)
    return batches, record_count


def load_prediction_corpus(settings: Any) -> PredictionCorpus:
    """Load the complete archive without writing indexes, caches or source files."""
    warnings: list[str] = []
    drafts = _draft_batches(Path(settings.runtime_dir), warnings)
    formals, formal_records = _formal_batches(Path(settings.history_path), warnings)
    exports, export_records = _export_batches(Path(settings.project_root), warnings)
    batches = [*drafts, *formals, *exports]

    def sort_key(batch: PredictionBatch) -> tuple[datetime, str, str]:
        parsed = _parse_datetime(batch.created_at)
        if parsed is None and batch.prediction_date:
            day = _parse_date(batch.prediction_date)
            parsed = datetime.combine(day, datetime.min.time(), CHINA) if day else None
        return (parsed.astimezone(timezone.utc) if parsed else datetime.min.replace(tzinfo=timezone.utc), batch.kind, batch.id)

    batches.sort(key=sort_key, reverse=True)
    return PredictionCorpus(
        batches=batches,
        warnings=warnings,
        counts={
            "draft_batches": len(drafts),
            "formal_batches": len(formals),
            "formal_records": formal_records,
            "export_batches": len(exports),
            "export_records": export_records,
        },
    )


def prediction_history_page(
    settings: Any,
    *,
    kind: HistoryKind = "all",
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    competition: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("分页参数超出范围")
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from 不能晚于 date_to")
    corpus = load_prediction_corpus(settings)
    needle = (q or "").strip().casefold()
    competition_key = (competition or "").strip().casefold()
    filtered: list[PredictionBatch] = []
    for batch in corpus.batches:
        if kind != "all" and batch.kind != kind:
            continue
        if needle and needle not in batch.search_text:
            continue
        if competition_key and competition_key not in {item.casefold() for item in batch.competitions}:
            continue
        if date_from or date_to:
            if not batch.filter_dates:
                continue
            if not any(
                (date_from is None or value >= date_from) and (date_to is None or value <= date_to)
                for value in batch.filter_dates
            ):
                continue
        filtered.append(batch)
    total = len(filtered)
    start = (page - 1) * page_size
    return {
        "items": [batch.summary() for batch in filtered[start:start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size),
        "counts": corpus.counts,
        "warnings": corpus.warnings,
    }


def prediction_history_detail(settings: Any, *, kind: DetailKind, batch_id: str) -> dict[str, Any]:
    pattern = SAFE_DRAFT_ID if kind == "draft" else SAFE_OPAQUE_ID
    if not pattern.fullmatch(batch_id):
        raise LookupError("历史预测批次不存在")
    corpus = load_prediction_corpus(settings)
    for batch in corpus.batches:
        if batch.kind == kind and batch.id == batch_id:
            return batch.detail()
    raise LookupError("历史预测批次不存在")
