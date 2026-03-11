import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .database import create_db_and_tables


def configure_logging() -> None:
    level_name = os.getenv("APP_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)

    # Reuse uvicorn's stderr handler so application logs show in container output.
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    if uvicorn_error_logger.handlers:
        app_logger.handlers = uvicorn_error_logger.handlers
        app_logger.propagate = False
        return

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Softplan API", version="0.1.0")

    allowed_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000,http://localhost:3001")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in allowed_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_event_handler("startup", create_db_and_tables)
    app.include_router(api_router)
    return app


app = create_app()
