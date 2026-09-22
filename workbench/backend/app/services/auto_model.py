"""Versioned on-demand models from source-verified, same-season official results.

This research lane never registers an active model or writes formal history.
The corpus accumulates across requests; team identity is never inferred by name.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

from ..schemas import Fixture
from .evidence import CHINA, _recent_row
from .history import atomic_json_write
from .model import ModelError
from .play_logic import aggregate_difference_probabilities, compatible, unique_pick
from .security import validate_redirect_chain


METHOD = "competition_2025_history_shrinkage_poisson_v3"
_CORPUS_LOCK = threading.Lock()
CORE = ("match_id", "match_date", "home_team_id", "away_team_id", "home_goals_90",
        "away_goals_90", "competition_id", "season_id", "status", "score_basis", "is_friendly")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _signature(row: dict) -> tuple:
    return tuple(row.get(key) for key in CORE)


def _valid_source(source: dict, cutoff: datetime) -> bool:
    try:
        timestamp = datetime.fromisoformat(source["fetched_at"])
        return (bool(source.get("source_id")) and timestamp.tzinfo is not None
                and timestamp <= cutoff and validate_redirect_chain([source["url"]])
                and bool(re.fullmatch(r"[0-9a-f]{64}", source["sha256"])))
    except (ValueError, TypeError, KeyError):
        return False


def _receipt_rows(evidence_dir: Path, wanted: set[str], cutoff: datetime) -> dict[str, set[tuple]]:
    """Recheck raw receipt hashes; a plausible source URL is not proof of a row."""
    result: dict[str, set[tuple]] = {}
    for path in (evidence_dir / "sources").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            source = record["source"]
            sid = source["source_id"]
            if sid not in wanted or not _valid_source(source, cutoff):
                continue
            if digest(record["payload"]) != source["sha256"]:
                continue
            value = record["payload"]["value"]
            raw_rows = []
            if source.get("kind") in {"recent", "training"}:
                for side in ("home", "away"):
                    raw_rows.extend((value.get(side) or {}).get("matchList") or [])
            elif source.get("kind") == "h2h":
                raw_rows = value.get("matchList") or []
            accepted = result.setdefault(sid, set())
            for raw in raw_rows:
                try:
                    accepted.add(_signature(_recent_row(raw, sid, h2h=source.get("kind") == "h2h")))
                except (ValueError, TypeError, AttributeError):
                    continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def _scope_key(scope: dict) -> str | None:
    ids = [scope.get("competition_id"), scope.get("season_id")]
    if not all(isinstance(i, str) and re.fullmatch(r"[1-9][0-9]{0,11}", i) for i in ids):
        return None
    return "-".join(ids)


def _pool(documents: list[dict], scope: dict, cutoff: datetime, receipts: dict, *, lookback_days: int = 180, row_field: str = "rows") -> tuple[list[dict], dict]:
    candidates: dict[str, list[dict]] = defaultdict(list)
    excluded: Counter = Counter()
    cutoff_day = cutoff.astimezone(CHINA).date()
    for doc in documents:
        sources = {s.get("source_id"): s for s in doc.get("sources", []) if _valid_source(s, cutoff)}
        for row in doc.get(row_field, []):
            try:
                if any(row.get(k) != scope.get(k) for k in ("competition_id", "season_id")):
                    excluded["different_competition_or_season"] += 1
                    continue
                sid = row.get("source_id")
                if sid not in sources or _signature(row) not in receipts.get(sid, set()):
                    excluded["source_not_verified"] += 1
                    continue
                if not all(re.fullmatch(r"[1-9][0-9]{0,11}", str(row.get(k, "")))
                           for k in ("match_id", "home_team_id", "away_team_id")):
                    excluded["incomplete_official_identity"] += 1
                    continue
                if row["home_team_id"] == row["away_team_id"]:
                    excluded["identity_conflict"] += 1
                    continue
                age = (cutoff_day - date.fromisoformat(row["match_date"])).days
                if not 0 < age <= lookback_days:
                    excluded["future_same_day_or_old"] += 1
                    continue
                if (row.get("status") != "completed_90" or row.get("score_basis") != "90_minutes"
                        or row.get("is_friendly") is not False):
                    excluded["unverified_90_or_friendly"] += 1
                    continue
                if not all(type(row.get(k)) is int and 0 <= row[k] <= 99 for k in ("home_goals_90", "away_goals_90")):
                    excluded["invalid_score"] += 1
                    continue
                candidates[row["match_id"]].append(row)
            except (ValueError, KeyError, TypeError):
                excluded["malformed_row"] += 1
    rows = []
    for versions in candidates.values():
        if len({_signature(r) for r in versions}) != 1:
            excluded["conflicting_match"] += len(versions)
            continue
        rows.append(versions[0])
        excluded["duplicate_observation"] += len(versions) - 1
    return sorted(rows, key=lambda r: (r["match_date"], r["match_id"])), dict(excluded)


def _documents(bundle: dict, evidence_dir: Path, root: Path, cutoff: datetime) -> tuple[list[dict], list[str]]:
    scope = bundle.get("scope", {})
    key = _scope_key(scope)
    if not key:
        return [], ["官方详情未确认赛事ID或赛季ID"]
    rows = []
    for team in bundle.get("teams", {}).values():
        rows.extend(team.get("recent_matches", []))
        rows.extend(team.get("training_matches", []))
    rows.extend(bundle.get("h2h", {}).get("matches", []))
    prior_rows = [r for team in bundle.get("teams", {}).values() for r in team.get("historical_prior_matches", [])]
    document = {"schema_version": 1, "scope": scope, "collected_at": bundle.get("collected_at"),
                "fixture_id": bundle.get("match_id"), "rows": rows, "prior_rows": prior_rows, "sources": bundle.get("sources", [])}
    folder = root / "corpus" / key
    fingerprint = digest(document)
    path = folder / f"{fingerprint}.json"
    with _CORPUS_LOCK:
        if not path.exists():
            atomic_json_write(path, {"content_sha256": fingerprint, "content": document})
    documents, warnings = [], []
    for path in folder.glob("*.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            content = saved["content"]
            observed = datetime.fromisoformat(content["collected_at"])
            if digest(content) != saved["content_sha256"] or _scope_key(content["scope"]) != key:
                raise ValueError("corpus_integrity")
            if observed.tzinfo is not None and observed <= cutoff:
                documents.append(content)
        except (OSError, ValueError, TypeError, KeyError):
            warnings.append(f"训练档案校验失败，已隔离：{path.name}")
    return documents, warnings


def _cached_fit(rows, scope, *, root, cutoff, youth, prior_only):
    from .cold_start_model import ColdStartModel, temporal_evaluate
    # ColdStartModel excludes rows by Beijing calendar date, not by the
    # microsecond at which a concurrent request reaches the fitter.  Keep that
    # real input boundary in the cache identity so different as-of dates never
    # share a fit, while same-day requests can safely reuse identical rows.
    cutoff_day = cutoff.astimezone(CHINA).date().isoformat()
    key = digest({"method": METHOD, "rows": rows, "scope": scope, "youth": youth,
                  "prior_only": prior_only, "cutoff_day": cutoff_day})
    path = root / "fits" / f"{key}.json"
    with _CORPUS_LOCK:
        if path.exists():
            record = json.loads(path.read_text(encoding="utf8"))
            content = record["content"]
            if (digest(content) != record["sha256"] or content["fit_key"] != key
                    or content.get("cutoff_day") != cutoff_day):
                raise ModelError("已训练参数缓存校验失败，未静默覆盖")
            data = content["model"]
            stamp = datetime.fromisoformat(data["cutoff"])
            if stamp.tzinfo is None or stamp.astimezone(CHINA).date().isoformat() != cutoff_day:
                raise ModelError("已训练参数缓存截止日与本次请求不一致")
            # A later same-day request may win the cache race.  Parameters are
            # still identical because rows and the day-level exclusion boundary
            # are in the key; expose no timestamp later than this caller knew.
            request_stamp = min(stamp, cutoff)
            model = ColdStartModel(competition_id=data["competition_id"], season_id=data["season_id"], cutoff=request_stamp,
                baseline_rate=data["baseline_rate"], teams=data["teams"], attack=data["attack"], defense=data["defense"],
                team_match_counts=data["team_match_counts"], training_match_count=data["training_match_count"],
                valid_rows_sha256=data["valid_rows_sha256"], excluded_rows=data["excluded_rows"],
                fit_status=data["fit_status"], optimizer=data["optimizer"])
            return model, content["evaluation"], True
        model = ColdStartModel.fit(rows, competition_id=scope["competition_id"], season_id=scope["season_id"],
                                   cutoff=cutoff, include_seasons=True, isolate_generations=youth)
        evaluation = (temporal_evaluate(rows, competition_id=scope["competition_id"], season_id=scope["season_id"],
                       include_seasons=True, isolate_generations=youth, max_folds=24) if not prior_only
                      else {"status": "prior_transfer_not_validated", "denominator": 0, "no_future_leakage": True})
        content = {"fit_key": key, "cutoff_day": cutoff_day, "model": {**model.to_dict(),
                   "model_type": "competition_2025_cross_season_regularized_poisson_map",
                   "prior_center_source": "same_competition_verified_90_minute_results_since_2025",
                   "generation_isolation": youth}, "evaluation": evaluation}
        atomic_json_write(path, {"content": content, "sha256": digest(content)})
        return model, evaluation, False


def build_auto_models(items: list[tuple[Fixture, dict]], bundles: list[dict], *, evidence_dir: Path,
                      allow_historical_download: bool = False, rebuild_history: bool = False,
                      workbench_dir: Path | None = None) -> list[tuple[dict, dict | None]]:
    from .cold_start_model import ColdStartModel, temporal_evaluate
    from .historical_samples import ensure_historical_samples, ensure_football_data_history, ensure_manifest_history, FOOTBALL_DATA_CODES

    root = evidence_dir.parent / "auto_models"
    now = datetime.now(timezone.utc)
    # Archive the entire batch before fitting: peer fixtures can add real
    # same-season rows without making the result depend on display order.
    for (fixture, _), bundle in zip(items, bundles, strict=True):
        if bundle.get("identity_verified") and fixture.kickoff_time.tzinfo and now < fixture.kickoff_time:
            _documents(bundle, evidence_dir, root, now)
    output = []
    histories = {}
    workbench_dir = workbench_dir or evidence_dir.parent.parent
    for (fixture, _), bundle in zip(items, bundles, strict=True):
        metadata = {"status": "unavailable", "training_matches": 0, "home_matches": 0,
                    "away_matches": 0, "prior_dominated": True, "source_count": 0,
                    "scope": bundle.get("scope", {}), "excluded": {},
                    "evaluation": {"status": "not_enough_data", "denominator": 0},
                    "limitations": ["2025-01-01起同赛事跨季研究模型，未经概率校准", "场地/阵容/伤停未进入参数，未学习主场加成", "历史缓存仅含实际获取样本，不能视为完整联赛"]}
        analysis = None
        try:
            if not bundle.get("identity_verified"):
                raise ModelError("球队身份未核实，自动建模已阻止")
            if fixture.kickoff_time.tzinfo is None or now >= fixture.kickoff_time:
                metadata["status"] = "blocked"
                raise ModelError("已开球或时间无时区，禁止建赛前模型")
            scope = bundle.get("scope", {})
            documents, warnings = _documents(bundle, evidence_dir, root, now)
            metadata["limitations"].extend(warnings)
            if not documents:
                raise ModelError("官方赛事/赛季身份未确认或无有效训练档案")
            home = bundle["teams"]["home"]["team_id"]
            away = bundle["teams"]["away"]["team_id"]
            if not home or not away or home == away:
                raise ModelError("球队ID缺失或冲突")
            cache_key = str(scope["competition_id"])
            if cache_key not in histories:
                histories[cache_key] = ensure_historical_samples(scope, evidence_dir=evidence_dir, bundles=bundles,
                    rebuild=rebuild_history, allow_download=allow_historical_download)
            history = histories[cache_key]
            training_scope = dict(scope)
            identity_mapping = None
            # Existing explicit aliases are a separate, inspectable bridge to a
            # name-domain dataset; no numeric official IDs are manufactured.
            qualifying = fixture.competition == "欧冠" and any(word in scope.get("phase_name", "").lower() for word in ("qualif", "资格", "preliminary"))
            if not qualifying and (fixture.competition in FOOTBALL_DATA_CODES or fixture.competition in {"欧冠", "日职", "世界杯"}):
                fd_key = "fd:" + fixture.competition
                if fd_key not in histories:
                    collector = ensure_football_data_history if fixture.competition in FOOTBALL_DATA_CODES else ensure_manifest_history
                    histories[fd_key] = collector(fixture.competition, runtime_dir=evidence_dir.parent,
                        rebuild=rebuild_history, allow_download=allow_historical_download)
                fd_history = histories[fd_key]
                aliases_path = workbench_dir / "config" / "team_aliases.json"
                if aliases_path.exists() and fd_history["rows"]:
                    from .routing import _strict_json
                    aliases = _strict_json(aliases_path).get("competitions", {}).get(fixture.competition, {})
                    names = {r[k] for r in fd_history["rows"] for k in ("home_team", "away_team")}
                    hname = aliases.get(fixture.home_team, fixture.home_team if fixture.home_team in names else None)
                    aname = aliases.get(fixture.away_team, fixture.away_team if fixture.away_team in names else None)
                    if hname and aname and hname != aname and hname in names and aname in names:
                        prefix = fd_history["scope"]["competition_id"] + ":"
                        identity_mapping = {"alias_path": str(aliases_path.resolve()),
                            "alias_sha256": hashlib.sha256(aliases_path.read_bytes()).hexdigest(),
                            "home": {"official_id": home, "source_name": fixture.home_team, "dataset_name": hname},
                            "away": {"official_id": away, "source_name": fixture.away_team, "dataset_name": aname},
                            "method": "existing_explicit_alias_only_no_fuzzy_matching"}
                        home, away = prefix + hname, prefix + aname
                        history = fd_history
                        training_scope = {"competition_id": fd_history["scope"]["competition_id"],
                                          "season_id": fd_history["rows"][-1]["season_id"]}
            rows, excluded = history["rows"], history["excluded"]
            metadata["excluded"] = excluded
            metadata["historical_cache"] = {k: history[k] for k in ("version", "cache_hit", "built_at", "requested_start", "requested_end", "coverage", "source_errors")}
            metadata["identity_mapping"] = identity_mapping
            metadata["historical_market_rows"] = [m for m in history.get("historical_market_rows", [])
                if home in {m["home_team_id"], m["away_team_id"]} or away in {m["home_team_id"], m["away_team_id"]}]
            metadata["historical_model_team_ids"] = {"home": home, "away": away}
            if not rows:
                # Preserve the precise integrity rejection counts for diagnosis.
                wanted = {s.get("source_id") for doc in documents for s in doc.get("sources", [])}
                _, legacy_excluded = _pool(documents, scope, min(now, fixture.kickoff_time), _receipt_rows(evidence_dir, wanted, now))
                metadata["excluded"].update(legacy_excluded)
                raise ModelError("2025年起本赛事暂无可核验90分钟历史样本；已保留采集结果，需有新来源后明确重建")
            youth = any(word in fixture.competition.lower() for word in ("亚运", "青年", "u17", "u19", "u20", "u21", "u23", "奥运"))
            metadata["generation_isolation"] = youth
            prior_only = youth and not any(r["season_id"] == scope["season_id"] for r in rows)
            if youth:
                metadata["limitations"].append("青年/亚运跨届球队参数隔离；旧届样本仅参与赛事进球环境，不能继承旧队实力")
            else:
                metadata["limitations"].append("同赛事跨季汇总球队历史；转会、换帅及阵容变化尚未直接建模，近期状态由独立动态证据通道复核")
            now = datetime.now(timezone.utc)
            if now >= fixture.kickoff_time:
                metadata["status"] = "blocked"
                raise ModelError("历史采集期间已开球，禁止生成赛前模型候选")
            model, evaluation, fit_cache_hit = _cached_fit(rows, training_scope, root=root,
                cutoff=min(now, fixture.kickoff_time), youth=youth, prior_only=prior_only)
            metadata["fit_cache_hit"] = fit_cache_hit
            prediction = model.predict(home, away, prior_only=prior_only)
            version = f"{_scope_key(scope)}-{uuid.uuid4().hex}"
            dataset_path = root / "datasets" / f"{version}.json"
            artifact_path = root / "models" / f"{version}.json"
            used_ids = {r["source_id"] for r in rows}
            sources = {s["source_id"]: s for s in history["sources"] if s["source_id"] in used_ids}
            atomic_json_write(dataset_path, {"scope": training_scope, "target_scope": scope, "prior_only": prior_only, "as_of": now.isoformat(), "rows": rows,
                                            "rows_sha256": digest(rows), "sources": list(sources.values()), "excluded": excluded})
            artifact = {"version": version, "dataset_path": str(dataset_path), "dataset_sha256": digest(rows),
                        "model": {**model.to_dict(), "model_type": "competition_2025_cross_season_regularized_poisson_map",
                                  "prior_center_source": "same_competition_verified_90_minute_results_since_2025"},
                        "prediction_mode": "competition_prior_only" if prior_only else "competition_2025_cross_season_model", "evaluation": evaluation, "created_at": now.isoformat(),
                        "historical_cache_version": history["version"], "identity_mapping": identity_mapping,
                        "cross_season": True, "generation_isolation": youth,
                        "freeze_eligible": False, "active_registry_modified": False}
            atomic_json_write(artifact_path, artifact)
            home_n = 0 if prior_only else model.team_match_counts.get(home, 0)
            away_n = 0 if prior_only else model.team_match_counts.get(away, 0)
            metadata.update(status="trained", version=version, training_matches=len(rows),
                            home_matches=home_n, away_matches=away_n, source_count=len(used_ids),
                            prior_dominated=prediction.get("prior_dominated", True) or len(rows) < 8,
                            training_cutoff=max(r["match_date"] for r in rows),
                            artifact_path=str(artifact_path), dataset_path=str(dataset_path), evaluation=evaluation)
            metadata["training_scope"] = training_scope
            metadata["prior_only"] = prior_only
            analysis = _analysis(fixture, rows, prediction, metadata, home, away)
        except (ModelError, ValueError, KeyError, TypeError, OSError) as exc:
            metadata["reason"] = str(exc)
        output.append((metadata, analysis))
    return output


def _analysis(fixture: Fixture, rows: list[dict], prediction: dict, metadata: dict, home: str, away: str) -> dict:
    rp, hp = aggregate_difference_probabilities({int(k): v for k, v in prediction["difference_probabilities"].items()}, fixture.official_handicap)
    result, handicap = unique_pick(rp), unique_pick(hp) if hp else None
    caveats = list(metadata["limitations"])
    if metadata["prior_dominated"]:
        caveats.append("先验主导：双方数据不足，球队偏差向本赛事学习基准收缩，不代表实力已确认")
    def margin(probs):
        values = sorted(probs.values(), reverse=True)
        return values[0] - values[1]
    if margin(rp) < 1e-8:
        result = None
        caveats.append("最高胜负概率并列，先验不支持唯一方向，等待球队证据")
    if hp and margin(hp) < 1e-8:
        handicap = None
        caveats.append("最高让球概率并列，不任意打破并列形成选项")
    # Sparse or tied evidence cannot itself be positive evidence for a draw.
    if result == "平" and (metadata["prior_dominated"] or margin(rp) < 0.03):
        result = None
        caveats.append("缺少足够球队证据或平局领先优势，撤下平局选项")
    boundary_ids = set()
    if fixture.official_handicap is not None:
        for r in rows:
            if metadata.get("generation_isolation") and r["season_id"] != metadata["scope"]["season_id"]:
                continue
            d = r["home_goals_90"] - r["away_goals_90"]
            for team, orientation in ((home, 1), (away, -1)):
                if team in {r["home_team_id"], r["away_team_id"]}:
                    team_diff = d if r["home_team_id"] == team else -d
                    if team_diff * orientation == -fixture.official_handicap:
                        boundary_ids.add(r["match_id"])
    if handicap == "让平" and (metadata["prior_dominated"] or margin(hp) < 0.03 or len(boundary_ids) < 2):
        handicap = None
        caveats.append("让平缺少两场独立精确边界事实或明显概率优势，已撤下")
    if result and handicap and not compatible(result, handicap, fixture.official_handicap):
        handicap = None
        caveats.append("两玩法边际最高选项不相容，撤下让球选项")
    sample_label = "2025年起同赛事90分钟赛果，仅用于进球基准先验" if metadata.get("prior_only") else "2025年起同赛事跨季90分钟赛果"
    summary = f"历史缓存去重{metadata['training_matches']}场{sample_label}，已拟合收缩模型；可用主队{metadata['home_matches']}场、客队{metadata['away_matches']}场。" + ("当前先验主导，低置信。" if metadata["prior_dominated"] else "研究候选，未校准。")
    return {"analysis_status": "available", "analysis_method": METHOD, "analysis_label": "自动训练：2025年起同赛事收缩 Poisson",
            "analysis_summary": summary, "analysis_result": result, "analysis_handicap_result": handicap,
            "raw_probabilities": {"result": rp, "handicap_result": hp}, "probability_status": "自动训练的独立原始研究概率，未校准",
            "confidence": {"result": "低", "handicap_result": "低" if handicap else None}, "risk": "高",
            "supporting_evidence": [summary], "major_counterevidence": caveats,
            "counterevidence_actions": [{"evidence": c, "effect": "保留低置信、高风险；不授予正式冻结资格"} for c in caveats],
            "missing": ["独立赛前样本的概率校准", "确认首发与场地"], "freeze_eligible": False,
            "method_parameters": {"handicap_exact_boundary_paths": len(boundary_ids)}}
