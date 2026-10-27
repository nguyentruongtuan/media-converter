import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.logging_config import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class InvalidMedia(AppError):
    status_code = 422
    code = "invalid_media"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


def _body(code: str, message: str, details=None) -> dict:
    body = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message))

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_body("http_error", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_body(
                "validation_error", "Request validation failed", details=jsonable_encoder(exc.errors())
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content=_body("internal_error", "Internal server error"))
