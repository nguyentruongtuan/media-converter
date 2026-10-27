import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import REGISTRY, make_asgi_app

from app.api import health, jobs, videos
from app.config import get_settings
from app.db import session_scope
from app.errors import register_exception_handlers
from app.logging_config import configure_logging, request_id_var
from app.metrics import MetricsMiddleware, PlatformCollector
from app.redis_client import get_redis

logger = logging.getLogger(__name__)
_collector_registered = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collector_registered
    settings = get_settings()
    if not _collector_registered:
        REGISTRY.register(PlatformCollector(get_redis, session_scope, settings))
        _collector_registered = True
    logger.info("API started", extra={"environment": settings.environment})
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(title="Media Converter", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    app.include_router(health.router)
    app.include_router(videos.router)
    app.include_router(jobs.router)
    app.mount("/metrics", make_asgi_app())
    return app


app = create_app()
