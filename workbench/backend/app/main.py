from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import WORKBENCH_DIR, settings
from .schemas import DailyPredictionRequest, FixtureBatch, ReviewRequest
from .services.freeze import FreezeError, FreezeService
from .services.backtest import save_backtest, walk_forward_backtest
from .services.history import build_history_index, history_page, sha256_file
from .services.model import GoalModel, ModelError, import_training_csv
from .services.datasets import load_dataset_manifest
from .services.prefilter import RulePrefilter
from .services.security import safe_child
from .services.snapshots import fetch_official_json, fetch_sporttery_slate, import_official_snapshot
from .services.workflows import run_daily_prediction, settle_review
from .services.routing import load_active_model_routing
from .services.evidence_llm import status as evidence_llm_status
from .services.fixture_authority import validate_fixture_authority
from .review_routes import router as manual_review_router
from .prediction_history_routes import router as prediction_history_router


app = FastAPI(title="中国体彩足球预测与复盘工作台", version="0.1.0")
app.include_router(manual_review_router)
app.include_router(prediction_history_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TrainRequest(BaseModel):
    csv_path: str
    competition: str
    model_type: str = Field(pattern="^(poisson|dixon_coles)$")
    decay: float = Field(default=0.003, ge=0, le=0.1)
    model_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")


class BacktestRequest(BaseModel):
    csv_path: str
    competition: str
    model_type: str = Field(pattern="^(poisson|dixon_coles)$")
    output_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    min_train: int = Field(default=80, ge=8)
    refit_every: int = Field(default=20, ge=1)


class SnapshotRequest(BaseModel):
    source_url: str
    redirect_chain: list[str] = []
    source_updated_at: str | None = None
    payload: dict[str, Any]


class OfficialFetchRequest(BaseModel):
    url: str
    authorized: bool = False


class OfficialSlateRequest(BaseModel):
    business_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_basis: Literal["all", "business_date", "kickoff_date"] = "business_date"


class FreezeRequest(BaseModel):
    source_path: str
    idempotency_key: str = Field(min_length=8, max_length=128)
    expected_history_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


@app.get("/api/health")
def health() -> dict[str, Any]:
    llm_state = evidence_llm_status() if settings.llm_enabled else {"available": False, "status": "disabled"}
    return {
        "status": "ok",
        "formal_write_enabled": settings.formal_write_enabled,
        "llm": f"Codex登录可用 / {settings.llm_model} {settings.llm_effort}" if llm_state["available"] else f"证据统计可用；大模型 {llm_state.get('reason') or llm_state['status']}",
        "llm_provider": llm_state,
        "evidence_fallback": True,
        "automatic_training": "same_competition_season_shrinkage_research",
        "dynamic_research_enabled": settings.dynamic_research_enabled,
        "official_auto_fetch": True,
        "official_fetch_mode": "page_open_once",
        "authority": "json",
        "sqlite": "not_implemented",
    }


@app.get("/api/data-quality")
def data_quality() -> dict[str, Any]:
    index = build_history_index(settings.history_path, settings.runtime_dir / "cache" / "history_index.json")
    prefilter = RulePrefilter(settings.rules_path, settings.rule_index_path)
    return {
        "history": {key: value for key, value in index.items() if key != "entries"},
        "record_count_consistent": index["declared_record_count"] == index["actual_record_count"],
        "rules": prefilter.status(),
    }


@app.get("/api/datasets")
def datasets() -> dict[str, Any]:
    return load_dataset_manifest(settings.runtime_dir)


@app.get("/api/historical-coverage")
def historical_coverage() -> dict[str, Any]:
    from .services.historical_samples import digest
    items, errors = [], []
    for pointer in sorted((settings.runtime_dir / "historical_samples").glob("*/latest.json")):
        try:
            index = json.loads(pointer.read_text(encoding="utf-8"))
            if not re.fullmatch(r"[0-9a-f]{32}", index["version"]):
                raise ValueError("invalid_version")
            document = json.loads((pointer.parent / "versions" / f"{index['version']}.json").read_text(encoding="utf-8"))
            if digest(document) != index["sha256"]:
                raise ValueError("hash_mismatch")
            items.append({"key": pointer.parent.name, **{key: document.get(key) for key in (
                "competition", "scope", "provider", "version", "built_at", "requested_start",
                "requested_end", "coverage", "source_errors")}, "source_count": len(document.get("sources", []))})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"key": pointer.parent.name, "reason": type(exc).__name__})
    return {"items": items, "errors": errors, "policy": "首次建模采集；后续预测只读历史缓存，动态资料每次重新检索"}


@app.get("/api/history")
def get_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    prediction_date: str | None = None,
    match_number: str | None = None,
) -> dict[str, Any]:
    return history_page(
        settings.history_path,
        page=page,
        page_size=page_size,
        prediction_date=prediction_date,
        match_number=match_number,
    )


@app.get("/api/drafts/latest")
def latest_saved_draft() -> dict[str, Any]:
    """Read an existing research draft; never refresh evidence or run a model."""
    paths = [p for p in (settings.runtime_dir / "drafts").glob("*.json")
             if re.fullmatch(r"[0-9a-f]{32}", p.stem)]
    for path in sorted(paths, key=lambda p: p.stat().st_mtime_ns, reverse=True):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("run_id") != path.stem or not isinstance(document.get("matches"), list):
                continue
            return {**document, "read_only_saved_draft": True}
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    raise HTTPException(status_code=404, detail="尚无可读取的候选草稿")


@app.post("/api/official/import")
def import_snapshot(request: SnapshotRequest) -> dict[str, Any]:
    try:
        return import_official_snapshot(
            request.payload,
            source_url=request.source_url,
            redirect_chain=request.redirect_chain,
            source_updated_at=request.source_updated_at,
            output_dir=settings.runtime_dir / "snapshots",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/official/fetch")
def fetch_snapshot(request: OfficialFetchRequest) -> dict[str, Any]:
    try:
        return fetch_official_json(
            request.url,
            output_dir=settings.runtime_dir / "snapshots",
            authorized=request.authorized,
        )
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/official/slate")
def official_slate(request: OfficialSlateRequest) -> dict[str, Any]:
    """页面打开单次获取或手动刷新；不轮询，不由预测/冻结/复盘触发。"""
    try:
        return fetch_sporttery_slate(
            request.business_date,
            output_dir=settings.runtime_dir / "snapshots",
            date_basis=request.date_basis,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/training/import-preview")
def preview_training(request: TrainRequest) -> dict[str, Any]:
    try:
        path = safe_child(settings.runtime_dir, request.csv_path)
        matches = import_training_csv(path)
        return {
            "matches": len(matches),
            "competitions": sorted({match.competition for match in matches}),
            "teams": len({team for match in matches for team in (match.home_team, match.away_team)}),
            "latest": max((match.kickoff_date for match in matches), default=None),
        }
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/training/train")
def train(request: TrainRequest) -> dict[str, Any]:
    try:
        path = safe_child(settings.runtime_dir, request.csv_path)
        matches = import_training_csv(path)
        model = GoalModel.fit(
            matches,
            competition=request.competition,
            model_type=request.model_type,
            decay=request.decay,
        )
        output = settings.runtime_dir / "models" / f"{request.model_name}.json"
        if output.exists():
            raise ValueError("模型版本已存在，不可覆盖")
        model.save(output)
        return {"status": "trained", "path": str(output), "metadata": model.to_dict()}
    except (ValueError, OSError, ModelError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/models")
def list_models(include_archived: bool = False) -> dict[str, Any]:
    items = []
    registry = json.loads((WORKBENCH_DIR / "config" / "active_models.json").read_text(encoding="utf-8"))
    active_names = {entry.get("model") for entry in registry.get("competitions", {}).values()
                    if entry.get("status") == "active_research"}
    archived_count = 0
    for path in sorted((settings.runtime_dir / "models").glob("*.json")):
        try:
            model = GoalModel.load(path)
            archived = model.artifact_schema_version < 2 or path.stem not in active_names
            archived_count += int(archived)
            if not archived or include_archived:
                items.append({"name": path.stem, "archived": archived,
                              "legacy_blocked": model.artifact_schema_version < 2, **model.to_dict()})
        except ModelError as exc:
            archived_count += 1
            if include_archived:
                items.append({"name": path.stem, "archived": True, "status": "invalid", "reason": str(exc)})
    return {"items": items, "archived_count": archived_count,
            "archive_policy": "旧版仅在归档视图可见；保存历史预测引用和回测依据，不用于新预测"}


@app.get("/api/backtests")
def list_backtests() -> dict[str, Any]:
    items = []
    for path in sorted((settings.runtime_dir / "reviews").glob("*backtest*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            items.append({
                "name": path.stem,
                "model_type": report.get("model_type"),
                "competition": report.get("competition"),
                "denominator": report.get("denominator"),
                "hits": report.get("hits"),
                "coverage": report.get("coverage"),
                "brier": report.get("brier"),
                "log_loss": report.get("log_loss"),
                "no_future_leakage": report.get("no_future_leakage"),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return {"items": items}


@app.post("/api/backtests/run")
def run_backtest(request: BacktestRequest) -> dict[str, Any]:
    try:
        source = safe_child(settings.runtime_dir, request.csv_path)
        output = settings.runtime_dir / "reviews" / f"{request.output_name}.json"
        if output.exists():
            raise ValueError("回测报告已存在，拒绝覆盖")
        report = walk_forward_backtest(
            import_training_csv(source), competition=request.competition,
            model_type=request.model_type, min_train=request.min_train,
            refit_every=request.refit_every,
        )
        save_backtest(report, output)
        return {"status": "completed", "path": str(output), "report": report}
    except (ValueError, OSError, ModelError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/predictions/daily")
def daily(request: DailyPredictionRequest) -> dict[str, Any]:
    try:
        fixtures = FixtureBatch(fixtures=request.fixtures).fixtures
        fixtures = validate_fixture_authority(fixtures, runtime_dir=settings.runtime_dir)
        prefilter = RulePrefilter(settings.rules_path, settings.rule_index_path)
        model_path = None
        model_paths = None
        team_aliases = None
        neutral_competitions: set[str] | None = None
        if request.model_version == "auto":
            model_paths, team_aliases, registry = load_active_model_routing(
                WORKBENCH_DIR,
                settings.runtime_dir,
            )
            neutral_competitions = {
                competition for competition, entry in registry.get("competitions", {}).items()
                if entry.get("neutral") is True
            }
        else:
            model_path = settings.runtime_dir / "models" / f"{request.model_version}.json"
            if not model_path.exists():
                raise ValueError("模型版本不存在")
        return run_daily_prediction(
            fixtures,
            model_path=model_path,
            model_paths=model_paths,
            team_aliases=team_aliases,
            neutral_competitions=neutral_competitions,
            prefilter=prefilter,
            rule_budget=request.rule_budget,
            output_dir=settings.runtime_dir / "drafts",
            evidence_dir=settings.runtime_dir / "evidence",
            llm_enabled=settings.llm_enabled,
            llm_model=settings.llm_model,
            llm_effort=settings.llm_effort,
            llm_timeout_seconds=settings.llm_timeout_seconds,
            dynamic_research_enabled=settings.dynamic_research_enabled,
        )
    except (ValueError, ModelError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/predictions/freeze")
def freeze(request: FreezeRequest) -> dict[str, Any]:
    try:
        source = safe_child(settings.runtime_dir, request.source_path)
        document = json.loads(source.read_text(encoding="utf-8-sig"))
        service = FreezeService(settings.project_root, settings.history_path, settings.runtime_dir / "backups")
        return service.commit(
            document,
            source_path=source,
            idempotency_key=request.idempotency_key,
            expected_history_sha256=request.expected_history_sha256.lower(),
            enabled=settings.formal_write_enabled,
        )
    except (FreezeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/reviews/settle")
def review() -> dict[str, Any]:
    """Retired: client-supplied verification must not create scored reports."""
    raise HTTPException(
        status_code=410,
        detail="旧复盘接口已停用；请使用 /api/manual-reviews/run，由服务核验官方90分钟赛果及赛前版本资格。",
    )
