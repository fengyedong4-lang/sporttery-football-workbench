"""Evidence fallback orchestration; candidate-only, bounded, fail visibly."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import Fixture
from .evidence import collect_fixture_evidence
from .evidence_analysis import analyze_evidence
from .evidence_llm import suggest_evidence_batch
from .auto_model import build_auto_models
from .play_logic import compatible
from .market_context import build_market_context
from .history import atomic_json_write
import hashlib
import json
import uuid


_LLM_SLOT = threading.Lock()


def _official_gate(fixture: Fixture, row: dict) -> None:
    def eligible(play):
        return play.status == "销售中" and all(v is not None for v in (play.home, play.draw, play.away))
    result = row.get("analysis_result")
    handicap = row.get("analysis_handicap_result")
    if fixture.official_handicap is None:
        handicap = None
    if result and handicap and not compatible(result, handicap, fixture.official_handicap):
        handicap = None
        row.setdefault("missing", []).append("两玩法分析不相容，让球方向已撤下")
    row["analysis_handicap_result"] = handicap
    row["result"] = result if eligible(fixture.result_play) else None
    row["handicap_result"] = handicap if eligible(fixture.handicap_play) else None
    row["result_status"] = ("分析候选" if result else "证据不足") if eligible(fixture.result_play) else fixture.result_play.status
    row["handicap_status"] = ("分析候选" if handicap else "证据不足") if eligible(fixture.handicap_play) else fixture.handicap_play.status
    if fixture.official_handicap is None:
        row["handicap_status"] = "官方数据暂未获取"
    row["freeze_eligible"] = False


def fill_evidence_fallbacks(
    items: list[tuple[Fixture, dict]], *, evidence_dir: Path,
    llm_enabled: bool = True, llm_model: str = "gpt-6-astra", llm_effort: str = "xhigh",
    llm_timeout_seconds: int = 360,
    dynamic_research_enabled: bool = False,
) -> dict:
    report = {"evidence_fetch_performed": bool(items), "llm_calls": 0, "llm_status": "not_needed", "llm_usage": None}
    if not items:
        return report

    def collect(item):
        fixture, _ = item
        try:
            return collect_fixture_evidence(fixture, output_dir=evidence_dir, include_training=False)
        except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
            return {"identity_verified": False, "sources": [], "facts": [], "teams": {},
                    "missing": [f"证据解析失败（{type(exc).__name__}）"], "warnings": []}

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="evidence") as pool:
        bundles = list(pool.map(collect, items))
    # Dynamic research is a separate, fresh request. Historical samples are
    # persisted by the model builder, rather than refetched on every prediction.
    live = {"status": "disabled", "calls": 0, "fixtures": []}
    if dynamic_research_enabled:
        from .live_research import collect_live_research
        live = collect_live_research([item[0] for item in items], evidence_dir=evidence_dir,
                                     model=llm_model, effort=llm_effort, timeout_seconds=llm_timeout_seconds)
    live_rows = {str(r.get("match_id", r.get("fixture_id", ""))): r for r in live.get("fixtures", [])}
    report["dynamic_research"] = {key: value for key, value in live.items() if key != "fixtures"}
    for (fixture, row), bundle in zip(items, bundles, strict=True):
        dynamic = live_rows.get(fixture.match_id, {"status": live.get("status", "unavailable"), "facts": [], "sources": [], "missing": ["动态检索未返回该比赛资料"] if dynamic_research_enabled else []})
        dynamic.setdefault("status", "completed" if dynamic.get("facts") else "partial" if dynamic.get("sources") else live.get("status", "unavailable"))
        dynamic.setdefault("started_at", live.get("started_at"))
        dynamic.setdefault("request_id", live.get("request_id"))
        row["live_research"] = dynamic
        for field in ("facts", "sources", "missing", "warnings"):
            bundle.setdefault(field, []).extend(dynamic.get(field, []))
        bundle["live_research_id"] = live.get("request_id")
        bundle["collected_at"] = datetime.now(timezone.utc).isoformat()
    automatic = build_auto_models(items, bundles, evidence_dir=evidence_dir,
                                  allow_historical_download=dynamic_research_enabled)
    report["auto_model_trained"] = sum(meta["status"] == "trained" for meta, _ in automatic)
    report["auto_model_attempted"] = len(items)
    llm_items = []
    for (fixture, row), bundle, (auto_meta, independent) in zip(items, bundles, automatic, strict=True):
        analysis = analyze_evidence(fixture, bundle)
        row.update(analysis)
        row["summary"] = analysis["analysis_summary"]
        row["evidence"] = bundle
        row["statistical_baseline"] = dict(analysis)
        row["model_status"] = "evidence_fallback"
        row["auto_model"] = auto_meta
        row["independent_model"] = independent
        if independent and analysis["analysis_status"] != "blocked":
            row.update(independent)
            row["summary"] = independent["analysis_summary"]
            row["model_status"] = "auto_trained"
        row["matched_rule_ids"] = []
        row["rule_application_status"] = "证据通道独立判断；未自动应用历史场景规则"
        row["market_context"] = build_market_context(fixture, row["live_research"],
                                                     historical_rows=auto_meta.get("historical_market_rows"))
        # Persist a real before-state before presenting model lessons to Astra.
        # This avoids reconstructing counterfactual directions after the result.
        baseline_record = {"created_at": datetime.now(timezone.utc).isoformat(), "match_id": fixture.match_id,
                           "analysis": independent or analysis, "market_context": row["market_context"]}
        serialized = json.dumps(baseline_record, ensure_ascii=False, sort_keys=True, allow_nan=False)
        before_id = uuid.uuid4().hex
        atomic_json_write(evidence_dir / "independent" / f"{before_id}.json", baseline_record)
        row["independent_before_lessons"] = {"id": before_id, "created_at": baseline_record["created_at"],
                                            "sha256": hashlib.sha256(serialized.encode()).hexdigest(),
                                            "analysis_result": (independent or analysis).get("analysis_result"),
                                            "analysis_handicap_result": (independent or analysis).get("analysis_handicap_result")}
        if dynamic_research_enabled:
            from .manual_review import load_applicable_lessons
            row["model_lessons"] = load_applicable_lessons(evidence_dir.parent, {
                **fixture.model_dump(mode="json"), "model_status": row.get("model_status"),
                "analysis_method": row.get("analysis_method"),
                "model_method": (row.get("independent_model") or row.get("statistical_baseline") or row).get("analysis_method")})
        else:
            row["model_lessons"] = []
        row["llm_status"] = "not_requested"
        if bundle.get("identity_verified") and bundle.get("facts") and analysis["analysis_status"] != "blocked":
            llm_items.append({
                "fixture": {"match_id": fixture.match_id, "teams": {"home": fixture.home_team, "away": fixture.away_team},
                            "competition": fixture.competition, "kickoff_time": fixture.kickoff_time.isoformat(), "official_handicap": fixture.official_handicap},
                "evidence": bundle, "baseline": independent or analysis, "market_context": row["market_context"],
                "model_lessons": row["model_lessons"],
            })
        _official_gate(fixture, row)

    llm = {"status": "disabled" if not llm_enabled else "not_needed", "calls": 0, "usage": None, "matches": [], "reason": None}
    if llm_enabled and llm_items:
        if _LLM_SLOT.acquire(blocking=False):
            try:
                llm = suggest_evidence_batch(llm_items, model=llm_model, effort=llm_effort, timeout_seconds=llm_timeout_seconds, work_dir=evidence_dir / "analysis-temp")
            finally:
                _LLM_SLOT.release()
        else:
            llm.update(status="unavailable", reason="analysis_busy")
    report.update(llm_status=llm["status"], llm_calls=llm["calls"], llm_usage=llm["usage"], llm_reason=llm.get("reason"), llm_model=llm_model, llm_warnings=llm.get("warnings", []))
    report["analysis_llm_calls"] = llm["calls"]
    report["research_llm_calls"] = live.get("calls", 0)
    report["total_llm_calls"] = llm["calls"] + (live.get("calls") or 0)
    suggestions = {r["match_id"]: r for r in llm.get("matches", [])} if llm["status"] == "completed" else {}
    submitted = {item["fixture"]["match_id"] for item in llm_items}
    for fixture, row in items:
        row["llm_status"] = llm["status"] if fixture.match_id in submitted else "not_needed"
        row["llm_reason"] = llm.get("reason")
        suggestion = suggestions.get(fixture.match_id)
        if suggestion:
            suggestion = dict(suggestion)
            # A citation alone does not prove dominance of one exact margin.
            # This special play needs the independent baseline boundary check.
            boundary_baseline = row.get("independent_model") or row["statistical_baseline"]
            if suggestion["analysis_handicap_result"] == "让平" and boundary_baseline.get("analysis_handicap_result") != "让平":
                suggestion["analysis_handicap_result"] = None
                suggestion["handicap_reason"] += "；精确净胜球边界未通过独立统计检查，已撤下让平方向。"
                suggestion["missing"] = [*suggestion["missing"], "让平所需的独立精确边界优势"]
            facts = {fact["fact_id"]: fact for fact in row["evidence"]["facts"]}
            support = [facts[f]["summary"] for f in suggestion["support_fact_ids"]]
            counter = [facts[f]["summary"] for f in suggestion["counter_fact_ids"]]
            row.update(
                analysis_status="available" if suggestion["analysis_result"] or suggestion["analysis_handicap_result"] else "insufficient_evidence",
                analysis_method="codex_evidence_v1", analysis_label=f"Codex证据解读（{llm_model}，非专属训练模型）",
                analysis_result=suggestion["analysis_result"], analysis_handicap_result=suggestion["analysis_handicap_result"],
                summary=suggestion["summary"], analysis_summary=suggestion["summary"],
                supporting_evidence=support, major_counterevidence=counter,
                counterevidence_actions=[{"evidence": "；".join(counter) or "资料缺口与样本限制", "effect": suggestion["counterevidence_effect"]}],
                missing=list(dict.fromkeys([*row["missing"], *suggestion["missing"]])),
                confidence={"result": suggestion["confidence"], "handicap_result": suggestion["confidence"] if suggestion["analysis_handicap_result"] else None},
                raw_probabilities=None, probability_status="证据定性分析，不生成伪精确概率；独立统计基线另存",
                handicap_reason=suggestion["handicap_reason"], support_fact_ids=suggestion["support_fact_ids"], counter_fact_ids=suggestion["counter_fact_ids"],
                lesson_assessments=suggestion.get("lesson_assessments", []),
            )
        elif fixture.match_id in submitted and llm["status"] != "completed":
            row["missing"].append(f"大模型证据解读未完成：{llm.get('reason') or llm['status']}；已保留本地统计和原始证据")
        # A slow source/model response may finish after kickoff. Never backdate it.
        if fixture.kickoff_time.tzinfo is None or datetime.now(timezone.utc) >= fixture.kickoff_time:
            row.update(analysis_status="blocked", analysis_result=None, analysis_handicap_result=None,
                       raw_probabilities=None, summary="分析完成时已开赛或时间无时区，不生成赛前候选")
        _official_gate(fixture, row)
        row["lesson_application"] = {
            "lesson_ids": [str(l.get("lesson_id", l.get("id", ""))) for l in row.get("model_lessons", [])],
            "before": row.get("independent_before_lessons"),
            "after": {"analysis_result": row.get("analysis_result"), "analysis_handicap_result": row.get("analysis_handicap_result")},
            "assessments": row.get("lesson_assessments", []),
            "evaluation": "多因素共同分析；不能将差异认定为单条经验的独立贡献" if row.get("model_lessons") else "无匹配赛事经验",
        }
    return report
