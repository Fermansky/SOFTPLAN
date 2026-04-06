"""LLM 代理路由。

职责：
1. 暴露 backend 对外的 LLM availability 与 chat 接口。
2. 在聊天场景下复用已落库的 ExtractedImage 作为图片输入。
3. 将存储层、llm-service 层错误映射为合适的 HTTP 状态码。

说明：
- 本模块负责 API 编排和错误映射，不直接调用上游 OpenAI-compatible 接口。
- 图片附件只接受系统内已存在的 `ExtractedImage.id`。
"""

import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from minio.error import S3Error
from pydantic import BaseModel, Field
from sqlmodel import Session

from ...core.logging import build_log_extra, get_request_id
from ...database import get_session
from ...services import LlmImageUrlInputPart, LlmServiceClient, LlmTextInputPart, MinioStorage
from ..dependencies import get_extracted_image_or_404, get_llm_service_client, get_minio_storage

router = APIRouter(prefix="/llm", tags=["llm"])
logger = logging.getLogger(__name__)

_MAX_EXTRACTED_IMAGES_PER_CHAT = 4
_MAX_EXTRACTED_IMAGE_BYTES = 5 * 1024 * 1024


class LlmAvailabilityRead(BaseModel):
    """LLM 服务可用性响应。"""

    available: bool
    service: str
    health_path: str | None = None
    error: str | None = None


class LlmChatRequest(BaseModel):
    """对外聊天请求。"""

    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_id: str | None = None
    extracted_image_ids: list[int] | None = None


class LlmUsageRead(BaseModel):
    """token 用量响应。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmChatRead(BaseModel):
    """聊天响应。"""

    text: str
    model: str
    usage: LlmUsageRead
    request_id: str | None = None


@router.get("/availability", response_model=LlmAvailabilityRead)
def get_llm_availability(client: LlmServiceClient = Depends(get_llm_service_client)) -> LlmAvailabilityRead:
    """检查 llm-service 存活状态。"""
    available, error = client.check_availability()
    if available:
        return LlmAvailabilityRead(
            available=True,
            service="llm-service",
            health_path="/health",
        )

    logger.warning(
        "llm-service is unavailable",
        extra=build_log_extra("llm.availability.unavailable", error=error),
    )
    return LlmAvailabilityRead(
        available=False,
        service="llm-service",
        error=error,
    )


def _to_data_url(payload: bytes, *, content_type: str) -> str:
    """将图片字节转换为 data URL，供 llm-service 多模态输入复用。"""
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _build_extracted_image_input_parts(
    extracted_image_ids: list[int],
    *,
    session: Session,
    storage: MinioStorage,
) -> list[LlmImageUrlInputPart]:
    """将 extracted image 列表转换为聊天附件输入块。"""
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
    client: LlmServiceClient = Depends(get_llm_service_client),
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> LlmChatRead:
    """处理对外聊天请求，并在需要时附带已落库图片。"""
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

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
        "Forwarding llm chat request",
        extra=build_log_extra(
            "llm.chat.started",
            request_id=resolved_request_id,
            prompt_length=len(prompt),
            extracted_image_count=len(extracted_image_ids),
            has_custom_model=bool(payload.model),
        ),
    )

    result, error = client.chat(
        prompt=prompt,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        request_id=resolved_request_id,
        input_parts=input_parts,
    )
    if error is not None or result is None:
        logger.warning(
            "llm-service chat failed",
            extra=build_log_extra(
                "llm.chat.failed",
                request_id=resolved_request_id,
                extracted_image_count=len(extracted_image_ids),
                error=error,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"llm-service chat failed: {error}",
        )

    logger.info(
        "llm-service chat succeeded",
        extra=build_log_extra(
            "llm.chat.succeeded",
            request_id=result.request_id,
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
