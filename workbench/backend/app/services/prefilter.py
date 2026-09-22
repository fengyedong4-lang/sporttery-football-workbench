from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .history import sha256_file


MANDATORY_WORDS = ("禁止", "不得", "必须", "不可", "不允许")


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class RulePrefilter:
    def __init__(self, rules_path: Path, index_path: Path) -> None:
        self.rules_path = rules_path
        self.index_path = index_path

    def status(self) -> dict[str, Any]:
        source_hash = sha256_file(self.rules_path)
        index = json.loads(self.index_path.read_text(encoding="utf-8-sig"))
        serialized = _text(index)
        return {
            "source_sha256": source_hash,
            "index_mentions_source_hash": source_hash.lower() in serialized.lower(),
            "index_path": str(self.index_path),
        }

    def match(self, context: dict[str, Any], budget: int = 6) -> list[dict[str, Any]]:
        data = json.loads(self.rules_path.read_text(encoding="utf-8-sig"))
        rules = data.get("rules", [])
        terms = {
            str(value).lower()
            for key in ("competition", "match_type", "strength_type", "official_handicap", "scenario")
            if (value := context.get(key)) not in (None, "")
        }
        scored: list[tuple[int, bool, dict[str, Any]]] = []
        for rule in rules:
            body = _text(rule).lower()
            score = sum(3 for term in terms if term in body)
            mandatory = any(word in body for word in MANDATORY_WORDS)
            if score or mandatory:
                scored.append((score, mandatory, rule))
        scored.sort(key=lambda item: (not item[1], -item[0], str(item[2].get("rule_id", item[2].get("id", "")))))
        mandatory_rules = [rule for _, mandatory, rule in scored if mandatory]
        normal_rules = [rule for _, mandatory, rule in scored if not mandatory]
        selected = mandatory_rules + normal_rules[: max(0, budget - len(mandatory_rules))]
        return selected


def compact_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule.get("rule_id", rule.get("id")),
        "status": rule.get("status"),
        "scope": rule.get("scope", rule.get("applicable_scope")),
        "text": rule.get("rule_text", rule.get("text", rule.get("content"))),
    }

