from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_history_index(history_path: Path, output_path: Path) -> dict[str, Any]:
    source_hash = sha256_file(history_path)
    if output_path.exists():
        cached = json.loads(output_path.read_text(encoding="utf-8"))
        if cached.get("source_sha256") == source_hash:
            return cached
    data = json.loads(history_path.read_text(encoding="utf-8-sig"))
    records = data.get("records", [])
    entries: list[dict[str, Any]] = []
    natural_matches: set[tuple[str, str]] = set()
    versions: Counter[str] = Counter()
    for offset, record in enumerate(records):
        date = str(record.get("prediction_date") or "")
        number = str(record.get("match_number") or "")
        natural_matches.add((date, number))
        versions[str(record.get("prediction_version") or "unknown")] += 1
        entries.append(
            {
                "offset": offset,
                "prediction_date": date,
                "match_number": number,
                "match_id": record.get("match_id"),
                "competition": record.get("competition"),
                "version": record.get("prediction_version"),
                "state": record.get("state"),
            }
        )
    result = {
        "schema_version": 1,
        "source": str(history_path),
        "source_sha256": source_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "declared_record_count": data.get("record_count"),
        "actual_record_count": len(records),
        "independent_match_count": len(natural_matches),
        "version_counts": dict(versions),
        "entries": entries,
    }
    atomic_json_write(output_path, result)
    return result


def history_page(
    history_path: Path,
    *,
    page: int,
    page_size: int,
    prediction_date: str | None = None,
    match_number: str | None = None,
) -> dict[str, Any]:
    data = json.loads(history_path.read_text(encoding="utf-8-sig"))
    records = data.get("records", [])
    filtered = [
        record
        for record in records
        if (not prediction_date or record.get("prediction_date") == prediction_date)
        and (not match_number or record.get("match_number") == match_number)
    ]
    start = (page - 1) * page_size
    return {"items": filtered[start : start + page_size], "total": len(filtered), "page": page, "page_size": page_size}

