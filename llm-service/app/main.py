"""llm-service application entrypoint."""

from fastapi import FastAPI

from .api.router import api_router
from .core.logging import configure_logging, install_request_id_middleware
from .services.llm_client import log_backend_proxy_config


def create_app() -> FastAPI:
    configure_logging("llm-service")
    app = FastAPI(title="Softplan LLM Service", version="0.1.0")
    install_request_id_middleware(app)
    app.add_event_handler("startup", log_backend_proxy_config)
    app.include_router(api_router)
    return app


app = create_app()
