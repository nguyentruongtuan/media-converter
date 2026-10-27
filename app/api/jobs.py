from collections.abc import Callable

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_enqueue
from app.api.serializers import job_out
from app.auth import Principal, get_principal, require_admin
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import NotFound
from app.models import JobStatus, TranscodeJob
from app.redis_client import get_redis
from app.schemas import JobList, JobOut
from app.services import dead_letter
from app.services import jobs as job_service

router = APIRouter(prefix="/v1", tags=["jobs"])


def load_job(db: Session, job_id: str, principal: Principal) -> TranscodeJob:
    job = db.get(TranscodeJob, job_id)
    if job is None or not principal.can_access(job.owner):
        raise NotFound("Job not found")
    return job


@router.get("/jobs", response_model=JobList)
def list_jobs(
    video_id: str | None = None,
    status: JobStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    query = select(TranscodeJob)
    if not principal.is_admin:
        query = query.where(TranscodeJob.owner == principal.name)
    if video_id:
        query = query.where(TranscodeJob.video_id == video_id)
    if status:
        query = query.where(TranscodeJob.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(TranscodeJob.created_at.desc()).limit(limit).offset(offset)).all()
    return JobList(items=[job_out(j, settings) for j in items], total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    return job_out(load_job(db, job_id, principal), settings)


@router.post("/jobs/{job_id}/retry", status_code=202, response_model=JobOut)
def retry_job(
    job_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
    enqueue: Callable[..., bool] = Depends(get_enqueue),
):
    job = load_job(db, job_id, principal)
    job_service.reset_failed_job(db, job)
    enqueue(job.id)
    return job_out(job, settings)


@router.get("/admin/dead-letter", dependencies=[Depends(require_admin)])
def list_dead_letter(limit: int = Query(100, ge=1, le=1000), settings: Settings = Depends(get_settings)):
    return {"items": dead_letter.list_entries(get_redis(), settings, limit)}
