import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import api_router
from .core.logging import build_log_extra, configure_logging, install_request_id_middleware
from .database import create_db_and_tables
from .services import (
    ExtractedImageSemanticPromptError,
    get_extracted_image_semantic_task_worker,
    get_layout_analysis_task_worker,
    is_extracted_image_semantic_task_worker_enabled,
    is_layout_analysis_task_worker_enabled,
    load_extracted_image_semantic_prompt,
    log_llm_service_config,
)



def _log_extracted_image_semantic_prompt_status() -> None:
    logger = logging.getLogger("app")
    try:
        load_extracted_image_semantic_prompt()
    except ExtractedImageSemanticPromptError as exc:
        logger.warning(
            "Extracted image semantic prompt is unavailable",
            extra=build_log_extra("extracted_image_semantic.prompt.unavailable", error=str(exc)),
        )



def create_app() -> FastAPI:
    configure_logging("backend")
    app = FastAPI(title="Softplan API", version="0.1.0")
    install_request_id_middleware(app)

    allowed_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000,http://localhost:3001")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in allowed_origins.split(",") if origin.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_event_handler("startup", create_db_and_tables)
    app.add_event_handler("startup", _log_extracted_image_semantic_prompt_status)
    app.add_event_handler("startup", log_llm_service_config)

    if is_layout_analysis_task_worker_enabled():
        layout_analysis_worker = get_layout_analysis_task_worker()

        async def _start_layout_analysis_task_worker() -> None:
            await layout_analysis_worker.start()

        async def _stop_layout_analysis_task_worker() -> None:
            await layout_analysis_worker.stop()

        app.add_event_handler("startup", _start_layout_analysis_task_worker)
        app.add_event_handler("shutdown", _stop_layout_analysis_task_worker)

    if is_extracted_image_semantic_task_worker_enabled():
        extracted_image_semantic_worker = get_extracted_image_semantic_task_worker()

        async def _start_extracted_image_semantic_task_worker() -> None:
            await extracted_image_semantic_worker.start()

        async def _stop_extracted_image_semantic_task_worker() -> None:
            await extracted_image_semantic_worker.stop()

        app.add_event_handler("startup", _start_extracted_image_semantic_task_worker)
        app.add_event_handler("shutdown", _stop_extracted_image_semantic_task_worker)

    app.include_router(api_router)
    return app


app = create_app()
