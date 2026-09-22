from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import GoalModel, ModelError


def _strict_json(path: Path) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"JSON存在重复键: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def load_active_model_routing(workbench_dir: Path, runtime_dir: Path) -> tuple[dict[str, Path], dict[str, dict[str, str]], dict[str, Any]]:
    registry_path = workbench_dir / "config" / "active_models.json"
    aliases_path = workbench_dir / "config" / "team_aliases.json"
    registry = _strict_json(registry_path)
    aliases = _strict_json(aliases_path)
    paths: dict[str, Path] = {}
    for competition, entry in registry.get("competitions", {}).items():
        if entry.get("status") != "active_research":
            continue
        path = runtime_dir / "models" / f"{entry['model']}.json"
        if not path.exists():
            continue
        model = GoalModel.load(path)
        if model.artifact_schema_version < 2:
            raise ModelError(f"活动模型仍是旧结构: {competition}")
        if model.competition != competition:
            raise ModelError(f"活动模型登记赛事不一致: {competition}")
        unknown_targets = sorted(
            set(aliases.get("competitions", {}).get(competition, {}).values()) - set(model.teams)
        )
        if unknown_targets:
            raise ModelError(f"{competition} 队名映射指向模型外球队: {unknown_targets}")
        paths[competition] = path
    return paths, aliases.get("competitions", {}), registry
