import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from minio.error import S3Error
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..dependencies import get_extracted_image_or_404, get_llm_service_client, get_minio_storage
from ...database import get_session
from ...services import LlmImageUrlInputPart, LlmServiceClient, LlmTextInputPart, MinioStorage

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


@router.get("/availability", response_model=LlmAvailabilityRead)
def get_llm_availability(client: LlmServiceClient = Depends(get_llm_service_client)) -> LlmAvailabilityRead:
    logger.info("Checking llm-service availability")
    available, error = client.check_availability()
    if available:
        return LlmAvailabilityRead(
            available=True,
            service="llm-service",
            health_path="/health",
        )

    logger.warning("llm-service is unavailable: %s", error)
    return LlmAvailabilityRead(
        available=False,
        service="llm-service",
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
        content_type = (extracted_image.content_type or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Extracted image {image_id} is not an image resource",
            )

        try:
            payload = storage.download_bytes(extracted_image.storage_key, bucket=extracted_image.storage_bucket)
        except S3Error as exc:
            logger.warning(
                "Failed to download extracted image from MinIO image_id=%s storage_bucket=%s storage_key=%s error=%s",
                image_id,
                extracted_image.storage_bucket,
                extracted_image.storage_key,
                exc.code,
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
    client: LlmServiceClient = Depends(get_llm_service_client),
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> LlmChatRead:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

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
        "Forwarding llm chat request request_id=%s prompt_length=%s extracted_image_count=%s has_custom_model=%s",
        payload.request_id,
        len(prompt),
        len(extracted_image_ids),
        bool(payload.model),
    )

    result, error = client.chat(
        prompt=prompt,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        request_id=payload.request_id,
        input_parts=input_parts,
    )
    if error is not None or result is None:
        logger.warning(
            "llm-service chat failed request_id=%s extracted_image_count=%s error=%s",
            payload.request_id,
            len(extracted_image_ids),
            error,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"llm-service chat failed: {error}",
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

