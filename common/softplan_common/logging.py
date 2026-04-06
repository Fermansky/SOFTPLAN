"""Shared logging infrastructure for Softplan Python services."""

from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import os
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

try:
    from asgi_correlation_id import correlation_id as _request_id_context
except ImportError:
    _request_id_context = contextvars.ContextVar("request_id", default=None)

try:
    from pythonjsonlogger.jsonlogger import JsonFormatter as _BaseJsonFormatter
except ImportError:
    _BaseJsonFormatter = None

REQUEST_ID_HEADER = "X-Request-ID"
DEFAULT_LEGACY_REQUEST_ID_HEADER = "X-Convert-Task-Id"
_DEFAULT_REQUEST_ID_VALUE = "-"
_AUTO_JSON_ENVIRONMENTS = {"staging", "production"}
_STANDARD_LOG_RECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _to_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_environment() -> str:
    return os.getenv("APP_ENV", "development").strip().lower() or "development"


def _resolve_log_level_name() -> str:
    return os.getenv("APP_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).strip().upper() or "INFO"


def _resolve_log_format(environment: str) -> str:
    configured = os.getenv("APP_LOG_FORMAT", "auto").strip().lower() or "auto"
    if configured == "auto":
        return "json" if environment in _AUTO_JSON_ENVIRONMENTS else "console"
    if configured in {"console", "json"}:
        return configured
    return "console"


def _set_request_id(value: str | None):
    return _request_id_context.set(value)


def _reset_request_id(token: contextvars.Token[Any]) -> None:
    _request_id_context.reset(token)


def get_request_id() -> str | None:
    value = _request_id_context.get()
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def build_log_extra(event: str, **fields: Any) -> dict[str, Any]:
    extra = {"event": event}
    for key, value in fields.items():
        if value is not None:
            extra[key] = value
    return extra


class RequestContextFilter(logging.Filter):
    def __init__(self, *, service_name: str, environment: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = getattr(record, "service", self.service_name)
        record.environment = getattr(record, "environment", self.environment)
        record.logger = getattr(record, "logger", record.name)
        request_id = get_request_id()
        record.request_id = getattr(record, "request_id", request_id or _DEFAULT_REQUEST_ID_VALUE)
        return True


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)s | %(service)s | %(name)s | %(request_id)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


if _BaseJsonFormatter is not None:

    class JsonLogFormatter(_BaseJsonFormatter):
        def add_fields(self, log_record: dict[str, Any], record: logging.LogRecord, message_dict: dict[str, Any]) -> None:
            super().add_fields(log_record, record, message_dict)
            log_record.clear()
            log_record.update(
                {
                    "timestamp": _utc_timestamp(),
                    "level": record.levelname,
                    "service": getattr(record, "service", None),
                    "logger": getattr(record, "logger", record.name),
                    "message": record.getMessage(),
                }
            )

            request_id = getattr(record, "request_id", None)
            if request_id and request_id != _DEFAULT_REQUEST_ID_VALUE:
                log_record["request_id"] = request_id

            for key, value in record.__dict__.items():
                if key in _STANDARD_LOG_RECORD_FIELDS or key in log_record or value is None:
                    continue
                log_record[key] = value

            if record.exc_info:
                log_record["exc_info"] = self.formatException(record.exc_info)

else:

    class JsonLogFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload: dict[str, Any] = {
                "timestamp": _utc_timestamp(),
                "level": record.levelname,
                "service": getattr(record, "service", None),
                "logger": getattr(record, "logger", record.name),
                "message": record.getMessage(),
            }

            request_id = getattr(record, "request_id", None)
            if request_id and request_id != _DEFAULT_REQUEST_ID_VALUE:
                payload["request_id"] = request_id

            for key, value in record.__dict__.items():
                if key in _STANDARD_LOG_RECORD_FIELDS or key in payload or value is None:
                    continue
                payload[key] = value

            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)

            return json.dumps(payload, ensure_ascii=False)


def _build_logging_config(*, service_name: str, environment: str, log_level_name: str, access_enabled: bool) -> dict[str, Any]:
    formatter_name = _resolve_log_format(environment)
    uvicorn_access_handlers = ["default"] if access_enabled else []
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_context": {
                "()": RequestContextFilter,
                "service_name": service_name,
                "environment": environment,
            }
        },
        "formatters": {
            "console": {"()": ConsoleFormatter},
            "json": {"()": JsonLogFormatter},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["request_context"],
                "formatter": formatter_name,
            }
        },
        "root": {"handlers": ["default"], "level": log_level_name},
        "loggers": {
            "uvicorn.error": {"handlers": ["default"], "level": log_level_name, "propagate": False},
            "uvicorn.access": {
                "handlers": uvicorn_access_handlers,
                "level": log_level_name,
                "propagate": False,
            },
        },
    }


def configure_logging(service_name: str) -> None:
    environment = _resolve_environment()
    log_level_name = _resolve_log_level_name()
    access_enabled = _to_bool(os.getenv("APP_LOG_ACCESS_ENABLED"), default=True)
    logging.config.dictConfig(
        _build_logging_config(
            service_name=service_name,
            environment=environment,
            log_level_name=log_level_name,
            access_enabled=access_enabled,
        )
    )
    logging.captureWarnings(True)
    logging.getLogger("app.logging").info(
        "Logging configured",
        extra=build_log_extra(
            "logging.configured",
            log_format=_resolve_log_format(environment),
            environment=environment,
            log_level=log_level_name,
            access_log_enabled=access_enabled,
            pid=os.getpid(),
        ),
    )


def install_request_id_middleware(
    app: FastAPI,
    *,
    legacy_header_name: str | None = None,
) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER)
        if request_id is not None:
            request_id = request_id.strip() or None

        if request_id is None and legacy_header_name:
            legacy_request_id = request.headers.get(legacy_header_name)
            if legacy_request_id is not None:
                request_id = legacy_request_id.strip() or None

        if request_id is None:
            request_id = uuid4().hex

        token = _set_request_id(request_id)
        request.state.request_id = request_id
        request.state.request_started_at = perf_counter()
        try:
            response = await call_next(request)
        finally:
            _reset_request_id(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


__all__ = [
    "DEFAULT_LEGACY_REQUEST_ID_HEADER",
    "REQUEST_ID_HEADER",
    "ConsoleFormatter",
    "JsonLogFormatter",
    "RequestContextFilter",
    "build_log_extra",
    "configure_logging",
    "get_request_id",
    "install_request_id_middleware",
]
