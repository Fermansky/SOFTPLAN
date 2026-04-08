"""LLM API routes and configuration management."""

import base64
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from minio.error import S3Error
from pydantic import BaseModel, Field
from sqlmodel import Session

from ...core.logging import build_log_extra, get_request_id
from ...database import get_session
from ...models import LlmConfigCreate, LlmConfigListItem, LlmConfigRead, LlmConfigUpdate
from ...services import (
    LlmChatPersistenceError,
    LlmConfigConflictError,
    LlmConfigDisabledError,
    LlmConfigNotFoundError,
    LlmConfigResolutionError,
    LlmConfigValidationError,
    LlmImageUrlInputPart,
    LlmTextInputPart,
    MinioStorage,
    activate_llm_config,
    create_llm_config,
    delete_llm_config,
    get_active_llm_config,
    get_llm_config_or_raise,
    get_llm_service_client,
    list_llm_configs,
    serialize_llm_config,
    serialize_llm_config_list_item,
    update_llm_config,
)
from ..dependencies import get_extracted_image_or_404, get_minio_storage

router = APIRouter(prefix="/llm", tags=["llm"])
logger = logging.getLogger(__name__)

_MAX_EXTRACTED_IMAGES_PER_CHAT = 4
_MAX_EXTRACTED_IMAGE_BYTES = 5 * 1024 * 1024


class LlmAvailabilityRead(BaseModel):
    available: bool
    service: str
    health_path: str | None = None
    error: str | None = None


class LlmChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    config_id: UUID | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_id: str | None = None
    extracted_image_ids: list[int] | None = None


class LlmUsageRead(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmChatRead(BaseModel):
    text: str
    model: str
    usage: LlmUsageRead
    request_id: str | None = None



def _raise_llm_config_http_error(exc: Exception) -> None:
    if isinstance(exc, LlmConfigNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigDisabledError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigResolutionError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    raise exc


@router.get("/configs/active", response_model=LlmConfigRead)
def get_active_llm_config_route(session: Session = Depends(get_session)) -> LlmConfigRead:
    config = get_active_llm_config(session)
    if config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No active LLM config is configured")
    if not config.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="LLM config is disabled")
    return serialize_llm_config(config)


@router.get("/configs", response_model=list[LlmConfigListItem])
def list_llm_configs_route(session: Session = Depends(get_session)) -> list[LlmConfigListItem]:
    return [serialize_llm_config_list_item(config) for config in list_llm_configs(session)]


@router.post("/configs", response_model=LlmConfigRead, status_code=status.HTTP_201_CREATED)
def create_llm_config_route(payload: LlmConfigCreate, session: Session = Depends(get_session)) -> LlmConfigRead:
    try:
        config = create_llm_config(session, payload)
    except Exception as exc:
        _raise_llm_config_http_error(exc)
    return serialize_llm_config(config)


@router.get("/configs/{config_id}", response_model=LlmConfigRead)
def get_llm_config_route(config_id: UUID, session: Session = Depends(get_session)) -> LlmConfigRead:
    try:
        config = get_llm_config_or_raise(session, config_id)
    except Exception as exc:
        _raise_llm_config_http_error(exc)
    return serialize_llm_config(config)


@router.patch("/configs/{config_id}", response_model=LlmConfigRead)
def update_llm_config_route(
    config_id: UUID,
    payload: LlmConfigUpdate,
    session: Session = Depends(get_session),
) -> LlmConfigRead:
    try:
        config = update_llm_config(session, config_id, payload)
    except Exception as exc:
        _raise_llm_config_http_error(exc)
    return serialize_llm_config(config)


@router.post("/configs/{config_id}/activate", response_model=LlmConfigRead)
def activate_llm_config_route(config_id: UUID, session: Session = Depends(get_session)) -> LlmConfigRead:
    try:
        config = activate_llm_config(session, config_id)
    except Exception as exc:
        _raise_llm_config_http_error(exc)
    return serialize_llm_config(config)


@router.delete("/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_llm_config_route(config_id: UUID, session: Session = Depends(get_session)) -> Response:
    try:
        delete_llm_config(session, config_id)
    except Exception as exc:
        _raise_llm_config_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/availability", response_model=LlmAvailabilityRead)
def get_llm_availability(
    config_id: UUID | None = Query(default=None),
    session: Session = Depends(get_session),
) -> LlmAvailabilityRead:
    try:
        client = get_llm_service_client(config_id=config_id, session=session)
    except Exception as exc:
        _raise_llm_config_http_error(exc)

    available, error = client.check_availability()
    if available:
        return LlmAvailabilityRead(
            available=True,
            service="backend",
        )

    logger.warning(
        "Embedded LLM module is unavailable",
        extra=build_log_extra(
            "llm.availability.unavailable",
            config_id=str(config_id) if config_id is not None else str(client.config_id) if client.config_id is not None else None,
            config_code=client.config_code,
            error=error,
        ),
    )
    return LlmAvailabilityRead(
        available=False,
        service="backend",
        error=error,
    )



def _to_data_url(payload: bytes, *, content_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"



def _build_extracted_image_input_parts(
    extracted_image_ids: list[int],
    *,
    session: Session,
    storage: MinioStorage,
) -> list[LlmImageUrlInputPart]:
    if len(extracted_image_ids) > _MAX_EXTRACTED_IMAGES_PER_CHAT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"At most {_MAX_EXTRACTED_IMAGES_PER_CHAT} extracted images are allowed per chat request",
        )

    input_parts: list[LlmImageUrlInputPart] = []
    for image_id in extracted_image_ids:
        extracted_image = get_extracted_image_or_404(image_id, session)
        content_type = (extracted_image.content_type or "").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Extracted image {image_id} is not an image resource",
            )

        try:
            payload = storage.download_bytes(extracted_image.storage_key, bucket=extracted_image.storage_bucket)
        except S3Error as exc:
            logger.warning(
                "Failed to download extracted image from MinIO",
                extra=build_log_extra(
                    "llm.chat.extracted_image_download_failed",
                    image_id=image_id,
                    storage_bucket=extracted_image.storage_bucket,
                    storage_key=extracted_image.storage_key,
                    error_code=exc.code,
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Extracted image storage download failed: {exc.code}",
            ) from exc

        if len(payload) > _MAX_EXTRACTED_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Extracted image {image_id} exceeds the {_MAX_EXTRACTED_IMAGE_BYTES} byte limit for chat attachments"
                ),
            )

        input_parts.append(LlmImageUrlInputPart(url=_to_data_url(payload, content_type=content_type)))

    return input_parts


@router.post("/chat", response_model=LlmChatRead)
def chat(
    payload: LlmChatRequest,
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> LlmChatRead:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

    try:
        client = get_llm_service_client(config_id=payload.config_id, session=session)
    except Exception as exc:
        _raise_llm_config_http_error(exc)

    resolved_request_id = payload.request_id or get_request_id()
    extracted_image_ids = payload.extracted_image_ids or []
    input_parts = None
    if extracted_image_ids:
        image_input_parts = _build_extracted_image_input_parts(
            extracted_image_ids,
            session=session,
            storage=storage,
        )
        input_parts = [LlmTextInputPart(text=prompt), *image_input_parts]

    logger.info(
        "Forwarding llm chat request to embedded module",
        extra=build_log_extra(
            "llm.chat.started",
            request_id=resolved_request_id,
            config_id=str(client.config_id) if client.config_id is not None else None,
            config_code=client.config_code,
            prompt_length=len(prompt),
            extracted_image_count=len(extracted_image_ids),
            has_custom_model=bool(payload.model),
        ),
    )

    try:
        result, error = client.chat(
            prompt=prompt,
            system_prompt=payload.system_prompt,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_id=resolved_request_id,
            input_parts=input_parts,
        )
    except LlmChatPersistenceError as exc:
        logger.warning(
            "Embedded LLM persistence failed",
            extra=build_log_extra(
                "llm.chat.persistence_failed",
                request_id=resolved_request_id,
                config_id=str(client.config_id) if client.config_id is not None else None,
                config_code=client.config_code,
                extracted_image_count=len(extracted_image_ids),
                error=str(exc),
            ),
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if error is not None or result is None:
        logger.warning(
            "Embedded LLM chat failed",
            extra=build_log_extra(
                "llm.chat.failed",
                request_id=resolved_request_id,
                config_id=str(client.config_id) if client.config_id is not None else None,
                config_code=client.config_code,
                extracted_image_count=len(extracted_image_ids),
                error=error,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"llm chat failed: {error}",
        )

    logger.info(
        "Embedded LLM chat succeeded",
        extra=build_log_extra(
            "llm.chat.succeeded",
            request_id=result.request_id,
            config_id=str(client.config_id) if client.config_id is not None else None,
            config_code=client.config_code,
            response_model=result.model,
            total_tokens=result.usage.total_tokens,
            extracted_image_count=len(extracted_image_ids),
        ),
    )
    return LlmChatRead(
        text=result.text,
        model=result.model,
        usage=LlmUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
        ),
        request_id=result.request_id,
    )
