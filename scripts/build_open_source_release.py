"""Create a reviewed source distribution without altering local records.

The output must be a new directory. Private environments, downloaded source
bodies and repeated backups are excluded by an explicit inclusion list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "node_modules", "dist", "__pycache__", ".pytest_cache"}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".css", ".html", ".json", ".ini", ".txt", ".toml", ".js", ".mjs"}
ROOT_FILES = ("README.md", "LICENSE", "DATA_NOTICE.md", ".gitignore", ".gitattributes", "AGENTS.md", "football_rules.md", "data_structure_template.json")
DATA_FILES = ("prediction_history.json", "review_rules.json", "review_rule_index.json", "storage_policy.json")
DOC_FILES = ("README.md", "HISTORICAL-MODELS.md", "LIVE-RESEARCH.md", "MANUAL-REVIEW.md", "HISTORY-PREDICTIONS.md")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def selected_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for name in ROOT_FILES:
        files.add(root / name)
    for name in DATA_FILES:
        files.add(root / "data" / name)
    for folder, suffixes in (
        ("scripts", {".py"}), ("templates", {".md", ".json"}),
        ("workbench/backend", SOURCE_SUFFIXES),
        ("workbench/frontend", SOURCE_SUFFIXES),
        ("workbench/config", {".json"}),
        (".github/workflows", {".yml", ".yaml"}),
    ):
        for path in (root / folder).rglob("*"):
            relative = path.relative_to(root)
            if path.is_file() and not SKIP_PARTS.intersection(relative.parts) and path.suffix in suffixes:
                files.add(path)
    for name in DOC_FILES:
        path = root / "workbench" / name
        if path.exists():
            files.add(path)
    for name in ("setup-windows.ps1", "start-windows.ps1", "setup-macos.sh", ".gitignore"):
        files.add(root / "workbench" / name)
    # Explicit seed archives. Newly generated runtime files remain Git-ignored.
    for folder in ("drafts", "models", "auto_models", "model_lessons"):
        files.update((root / "workbench/runtime" / folder).rglob("*.json"))
    # Review reports are public seeds; their raw official source snapshots are not.
    reviews = root / "workbench/runtime/reviews"
    files.update(reviews.glob("*.json"))
    files.update((reviews / "manual").glob("*.json"))
    training_template = root / "workbench/runtime/imports/training_template.csv"
    if training_template.exists():
        files.add(training_template)
    for path in (root / "exports").rglob("prediction_V*.json"):
        if not any(re.search(r"backup|备份|修改前", part, re.I) for part in path.relative_to(root / "exports").parts):
            files.add(path)
    # Include active project rules, but never historical copies of protected files.
    for path in (root / "项目规则").glob("*.md"):
        files.add(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def sanitize_local_paths(text: str, root: Path) -> tuple[str, int]:
    variants = {str(root.resolve()), str(root.resolve()).replace("\\", "/")}
    variants |= {value.replace("\\", "\\\\") for value in variants}
    count = 0
    for value in sorted(variants, key=len, reverse=True):
        text, found = re.subn(re.escape(value), ".", text, flags=re.I)
        count += found
    return text, count


def credential_findings(text: str) -> list[str]:
    patterns = {
        "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        "github_credential": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b",
        "api_credential": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b",
        "credential_assignment": r'''(?i)(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)\s*[=:]\s*["'][A-Za-z0-9_+/=-]{24,}["']''',
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, text)]


def build(root: Path, output: Path) -> dict:
    root = root.resolve()
    output = output.resolve()
    if output.exists() or output == root or output in root.parents:
        raise ValueError("Output must be a new directory, not the source or its parent")
    prepared: list[tuple[Path, bytes, bytes, int]] = []
    for source in selected_files(root):
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Unsafe source path: {source.relative_to(root)}")
        raw = source.read_bytes()
        text = raw.decode("utf-8-sig")
        relative = source.relative_to(root)
        # Code is copied exactly, so test fixtures and path security checks stay intact.
        redact = relative.suffix in {".json", ".md", ".txt"}
        cleaned, changes = sanitize_local_paths(text, root) if redact else (text, 0)
        if findings := credential_findings(cleaned):
            raise ValueError(f"Credential-like content in {relative}: {','.join(findings)}")
        data = cleaned.encode("utf-8") if changes else raw
        if relative.suffix == ".json":
            json.loads(data.decode("utf-8-sig"))
        prepared.append((relative, raw, data, changes))
    # Keep the public rule index bound to the published rule bytes if redaction changed them.
    rules = next(data for path, _, data, _ in prepared if path.as_posix() == "data/review_rules.json")
    for index, (path, raw, data, changes) in enumerate(prepared):
        if path.as_posix() == "data/review_rule_index.json":
            doc = json.loads(data.decode("utf-8-sig"))
            if doc.get("source_sha256") != sha256(rules):
                doc["source_sha256"] = sha256(rules)
                data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                prepared[index] = (path, raw, data, changes)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1, "built_at": datetime.now(timezone.utc).isoformat(),
        "distribution": "source-and-path-redacted-prediction-archives",
        "original_records_unchanged": True,
        "exclusions": ["credentials", "environments", "dependency installs", "third-party source bodies", "raw source downloads", "official raw snapshots", "logs", "duplicate backups", "local handoff and acceptance notes"],
        "files": [],
    }
    for relative, raw, data, changes in prepared:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest["files"].append({"path": relative.as_posix(), "bytes": len(data), "sha256": sha256(data), "source_sha256": sha256(raw), "path_redactions": changes})
    (output / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = output.parent / f"{output.name}.zip"
    if archive.exists():
        raise ValueError("ZIP path already exists; no overwrite performed")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, arcname=f"{output.name}/{path.relative_to(output).as_posix()}")
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("ZIP integrity verification failed")
    return {"output": str(output), "archive": str(archive), "file_count": len(manifest["files"]), "bytes": sum(item["bytes"] for item in manifest["files"]), "archive_bytes": archive.stat().st_size, "archive_sha256": sha256(archive.read_bytes()), "path_redactions": sum(item["path_redactions"] for item in manifest["files"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
