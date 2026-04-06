from fastapi import FastAPI

from .api.router import api_router
from .core.logging import configure_logging, install_request_id_middleware


def create_app() -> FastAPI:
    configure_logging("file-convert-service")
    app = FastAPI(title="Softplan File Convert Service", version="0.1.0")
    install_request_id_middleware(app)
    app.include_router(api_router)
    return app


app = create_app()
