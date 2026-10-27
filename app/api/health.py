import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_storage_dep
from app.db import get_db
from app.redis_client import get_redis
from app.storage import StorageService

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
@router.get("/health/live")
def live() -> dict:
    """Liveness: the process is up and serving requests. No dependency checks."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db), storage: StorageService = Depends(get_storage_dep)):
    """Readiness: dependencies needed to serve traffic are reachable."""
    checks = {}
    for name, check in (
        ("database", lambda: db.execute(text("SELECT 1"))),
        ("redis", lambda: get_redis().ping()),
        ("storage", storage.ping),
    ):
        try:
            check()
            checks[name] = "ok"
        except Exception as exc:
            logger.warning("Readiness check failed", extra={"check": name, "error": str(exc)})
            checks[name] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
