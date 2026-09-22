from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .services import manual_review as service

router = APIRouter(prefix="/api/manual-reviews", tags=["manual-reviews"])


class RunReview(BaseModel):
    batch_id: str = Field(min_length=1, max_length=100)
    use_astra: bool = True


class AdoptLesson(BaseModel):
    match_id: str = Field(min_length=1, max_length=120)
    candidate_id: str = Field(pattern=r"^[0-9a-f]{20}$")


def call(fn, *args, **kwargs):
    try:
        return fn(settings, *args, **kwargs)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/batches")
def batches():
    return {"items": call(service.list_batches)}


@router.post("/run")
def run(request: RunReview):
    return call(service.run_review, request.batch_id, use_astra=request.use_astra)


@router.get("/reports")
def reports(batch_id: str | None = None):
    return {"items": call(service.list_reports, batch_id)}


@router.get("/reports/{report_id}")
def report(report_id: str):
    return call(service.read_report, report_id)


@router.post("/reports/{report_id}/lessons")
def lessons(report_id: str, request: AdoptLesson):
    return call(service.adopt_lesson, report_id, request.match_id, request.candidate_id)
