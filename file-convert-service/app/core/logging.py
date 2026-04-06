"""File convert service logging helpers backed by the shared package."""

from fastapi import FastAPI

from softplan_common.logging import (
    DEFAULT_LEGACY_REQUEST_ID_HEADER,
    REQUEST_ID_HEADER,
    ConsoleFormatter,
    JsonLogFormatter,
    RequestContextFilter,
    build_log_extra,
    configure_logging,
    get_request_id,
    install_request_id_middleware as _install_request_id_middleware,
)

LEGACY_REQUEST_ID_HEADER = DEFAULT_LEGACY_REQUEST_ID_HEADER


def install_request_id_middleware(app: FastAPI) -> None:
    _install_request_id_middleware(app, legacy_header_name=LEGACY_REQUEST_ID_HEADER)


__all__ = [
    "LEGACY_REQUEST_ID_HEADER",
    "REQUEST_ID_HEADER",
    "ConsoleFormatter",
    "JsonLogFormatter",
    "RequestContextFilter",
    "build_log_extra",
    "configure_logging",
    "get_request_id",
    "install_request_id_middleware",
]
