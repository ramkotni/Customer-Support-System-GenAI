"""Domain exceptions + centralized handlers (no internal leakage)."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.correlation import get_request_id
from app.core.errors import ErrorBody, ErrorResponse
from app.core.logging import get_logger

logger = get_logger("app.errors")


class AppError(Exception):
    """Base application error with a stable code + HTTP status."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(self, message: str, *, details: Any | None = None,
                 code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class BusinessRuleError(AppError):
    """A request is well-formed but violates a business/policy rule."""
    status_code = status.HTTP_409_CONFLICT
    code = "business_rule_violation"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class UpstreamError(AppError):
    """A dependency (DB, downstream service) failed."""
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "dependency_unavailable"


def _json(status_code: int, code: str, message: str, details: Any | None = None) -> JSONResponse:
    # `details` can carry non-JSON values — pydantic validation errors embed the
    # original exception object in `ctx`, for instance — so encode defensively.
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message,
                        details=jsonable_encoder(details) if details is not None else None,
                        request_id=get_request_id())
    )
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("app_error", extra={"code": exc.code, "detail": exc.message})
        return _json(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _json(422, "validation_error", "Request validation failed", exc.errors())

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                429: "rate_limited"}.get(exc.status_code, "http_error")
        return _json(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Never leak internal exception text/stack to clients.
        logger.exception("unhandled_exception")
        return _json(500, "internal_error", "An internal error occurred.")
