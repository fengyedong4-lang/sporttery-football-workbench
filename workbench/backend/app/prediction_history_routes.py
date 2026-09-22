from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from .config import settings
from .services.prediction_history import prediction_history_detail, prediction_history_page


router = APIRouter(prefix="/api/prediction-history", tags=["prediction-history"])


@router.get("")
def list_prediction_history(
    kind: Literal["all", "draft", "formal", "export"] = "all",
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    competition: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    try:
        return prediction_history_page(
            settings,
            kind=kind,
            q=q,
            date_from=date_from,
            date_to=date_to,
            competition=competition,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{kind}/{batch_id}")
def get_prediction_history(
    kind: Literal["draft", "formal", "export"],
    batch_id: str,
) -> dict:
    try:
        return prediction_history_detail(settings, kind=kind, batch_id=batch_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
