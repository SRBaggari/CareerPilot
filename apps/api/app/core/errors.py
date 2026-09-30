"""Domain errors and their HTTP mapping.

Services raise these instead of HTTPException so they stay independent of the web layer.
Validation errors use FastAPI's standard 422 shape so clients handle one format.
"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError


class DomainError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT


class ServiceUnavailableError(DomainError):
    """A dependency (e.g. the embedding service) is unavailable or misconfigured."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class FieldValidationError(DomainError):
    """A validation failure detected by a service (e.g. a cross-row rule)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class FieldErrors(DomainError):
    """Several field-level validation failures, reported together (HTTP 422)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _validation_detail(field: str, message: str) -> list[dict[str, Any]]:
    return [{"type": "value_error", "loc": ["body", field], "msg": message}]


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        if isinstance(exc, FieldValidationError):
            detail: Any = _validation_detail(exc.field, exc.message)
        elif isinstance(exc, FieldErrors):
            detail = [e for f, m in exc.errors.items() for e in _validation_detail(f, m)]
        else:
            detail = exc.message
        return JSONResponse(status_code=exc.status_code, content={"detail": detail})

    @app.exception_handler(ValidationError)
    async def _pydantic(_: Request, exc: ValidationError) -> JSONResponse:
        # Raised when a service re-validates merged data (e.g. a PATCH or an AI suggestion).
        errors = [
            {"type": e["type"], "loc": ["body", *e["loc"]], "msg": e["msg"]}
            for e in exc.errors(include_url=False, include_context=False)
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": jsonable_encoder(errors)},
        )
