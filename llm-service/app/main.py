from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.router import api_router
from .services.llm_client import log_openai_compatible_llm_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_openai_compatible_llm_config()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Softplan LLM Service", version="0.1.0", lifespan=lifespan)
    app.include_router(api_router)
    return app


app = create_app()
