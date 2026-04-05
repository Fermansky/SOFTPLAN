import base64
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException, status
from minio.error import S3Error

from ..models import ExtractedImage
from .llm_service import LlmServiceClient, LlmImageUrlInputPart, LlmTextInputPart
from .minio_storage import MinioStorage

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extracted_image_semantic.txt"
_DEFAULT_USER_PROMPT = "\u8bf7\u57fa\u4e8e\u8fd9\u5f20\u56fe\u7247\u751f\u6210\u4e00\u6bb5\u4e2d\u6587\u8bed\u4e49\u63cf\u8ff0\u3002"


@dataclass(frozen=True)
class ExtractedImageSemanticDescriptionResult:
    image_id: int
    description: str
    model: str
    request_id: str | None


class ExtractedImageSemanticPromptError(RuntimeError):
    """Raised when the semantic prompt configuration cannot be loaded."""


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def _to_data_url(payload: bytes, *, content_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _resolve_prompt_path() -> Path:
    configured_path = os.getenv("EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def load_extracted_image_semantic_prompt() -> str:
    prompt_path = _resolve_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        logger.warning("Extracted image semantic prompt file is missing path=%s", prompt_path)
        raise ExtractedImageSemanticPromptError(f"Prompt file not found: {prompt_path}") from exc
    except OSError as exc:
        logger.warning("Failed to read extracted image semantic prompt path=%s error=%s", prompt_path, exc)
        raise ExtractedImageSemanticPromptError(f"Failed to read prompt file: {prompt_path}") from exc

    if not prompt:
        logger.warning("Extracted image semantic prompt file is empty path=%s", prompt_path)
        raise ExtractedImageSemanticPromptError(f"Prompt file is empty: {prompt_path}")

    logger.info(
        "Loaded extracted image semantic prompt path=%s model_override=%s",
        prompt_path,
        os.getenv("EXTRACTED_IMAGE_SEMANTIC_MODEL") or "<default>",
    )
    return prompt


def get_extracted_image_semantic_model() -> str | None:
    value = os.getenv("EXTRACTED_IMAGE_SEMANTIC_MODEL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def resolve_extracted_image_semantic_model(requested_model: str | None = None) -> str | None:
    if requested_model is not None:
        stripped = requested_model.strip()
        if stripped:
            return stripped
    return get_extracted_image_semantic_model()


def describe_extracted_image_semantics(
    *,
    extracted_image: ExtractedImage,
    storage: MinioStorage,
    client: LlmServiceClient,
    request_id: str | None = None,
    model: str | None = None,
) -> ExtractedImageSemanticDescriptionResult:
    content_type = _normalize_content_type(extracted_image.content_type)
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Extracted image {extracted_image.id} is not an image resource",
        )

    prompt = load_extracted_image_semantic_prompt()

    try:
        payload = storage.download_bytes(extracted_image.storage_key, bucket=extracted_image.storage_bucket)
    except S3Error as exc:
        logger.warning(
            "Failed to download extracted image for semantic description image_id=%s storage_bucket=%s storage_key=%s error=%s",
            extracted_image.id,
            extracted_image.storage_bucket,
            extracted_image.storage_key,
            exc.code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Extracted image storage download failed: {exc.code}",
        ) from exc

    resolved_model = resolve_extracted_image_semantic_model(model)
    logger.info(
        "Generating extracted image semantic description image_id=%s storage_bucket=%s storage_key=%s file_size=%s model=%s request_id=%s has_custom_model=%s",
        extracted_image.id,
        extracted_image.storage_bucket,
        extracted_image.storage_key,
        len(payload),
        resolved_model or "<default>",
        request_id,
        bool(model and model.strip()),
    )
    result, error = client.chat(
        prompt=_DEFAULT_USER_PROMPT,
        system_prompt=prompt,
        model=resolved_model,
        request_id=request_id,
        input_parts=[
            LlmTextInputPart(text=_DEFAULT_USER_PROMPT),
            LlmImageUrlInputPart(url=_to_data_url(payload, content_type=content_type)),
        ],
    )
    if error is not None or result is None:
        logger.warning(
            "Semantic description llm call failed image_id=%s model=%s request_id=%s has_custom_model=%s error=%s",
            extracted_image.id,
            resolved_model or "<default>",
            request_id,
            bool(model and model.strip()),
            error,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"llm-service semantic description failed: {error}",
        )

    image_id = extracted_image.id if extracted_image.id is not None else 0
    return ExtractedImageSemanticDescriptionResult(
        image_id=image_id,
        description=result.text,
        model=result.model,
        request_id=result.request_id,
    )
