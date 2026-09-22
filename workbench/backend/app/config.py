from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
WORKBENCH_DIR = BACKEND_DIR.parent
PROJECT_ROOT = WORKBENCH_DIR.parent


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    runtime_dir: Path = Path(os.getenv("SPORTTERY_RUNTIME_DIR", WORKBENCH_DIR / "runtime"))
    history_path: Path = Path(
        os.getenv("SPORTTERY_HISTORY_PATH", PROJECT_ROOT / "data" / "prediction_history.json")
    )
    rules_path: Path = Path(
        os.getenv("SPORTTERY_RULES_PATH", PROJECT_ROOT / "data" / "review_rules.json")
    )
    rule_index_path: Path = Path(
        os.getenv("SPORTTERY_RULE_INDEX_PATH", PROJECT_ROOT / "data" / "review_rule_index.json")
    )
    formal_write_enabled: bool = os.getenv("SPORTTERY_ENABLE_FORMAL_WRITE", "0") == "1"
    llm_enabled: bool = os.getenv("SPORTTERY_EVIDENCE_LLM", "1") == "1"
    llm_model: str = os.getenv("SPORTTERY_EVIDENCE_MODEL", "gpt-6-astra")
    llm_effort: str = os.getenv("SPORTTERY_EVIDENCE_EFFORT", "xhigh")
    llm_timeout_seconds: int = min(600, max(1, int(os.getenv("SPORTTERY_EVIDENCE_TIMEOUT_SECONDS", "360"))))
    dynamic_research_enabled: bool = os.getenv("SPORTTERY_DYNAMIC_RESEARCH", "1") == "1"

    def ensure_runtime(self) -> None:
        for name in ("cache", "drafts", "models", "reviews", "snapshots", "backups", "jobs", "imports", "evidence"):
            (self.runtime_dir / name).mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_runtime()
