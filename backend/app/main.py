import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .database import create_db_and_tables


def create_app() -> FastAPI:
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

