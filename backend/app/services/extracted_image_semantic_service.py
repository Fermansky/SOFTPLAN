"""抽取图片语义识别执行服务。

职责：
1. 加载图片语义识别所需的提示词与默认模型配置。
2. 将已落库的 ExtractedImage 转换为内嵌 LLM 模块可消费的多模态输入。
3. 执行单次语义识别，并把结果统一映射为结构化执行结果。

说明：
- 本模块只负责编排“一次执行”，不负责任务落库与状态流转。
- 当未显式指定模型时，允许内嵌 LLM 模块使用其默认模型。
- 所有失败场景都转换为 `ExtractedImageSemanticExecutionResult`，由上层任务服务决定如何持久化。
"""

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from minio.error import S3Error

from ..models import ExtractedImage
from .llm_chat_persistence import LlmChatPersistenceError
from .llm_service import LlmImageUrlInputPart, LlmServiceClient, LlmTextInputPart
from .minio_storage import MinioStorage

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extracted_image_semantic.txt"
_DEFAULT_USER_PROMPT = "请基于这张图片生成一段中文语义描述。"
_DEFAULT_TARGET_MODEL_KEY = "__LLM_SERVICE_DEFAULT__"


@dataclass(frozen=True)
class ExtractedImageSemanticExecutionResult:
    """单次语义识别执行结果。"""

    succeeded: bool
    description: str | None = None
    result_model: str | None = None
    error_message: str | None = None


class ExtractedImageSemanticPromptError(RuntimeError):
    """图片语义提示词不可用。"""


def _normalize_content_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _to_data_url(payload: bytes, *, content_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def resolve_extracted_image_semantic_prompt_path() -> Path:
    configured_path = os.getenv("EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def load_extracted_image_semantic_prompt() -> str:
    prompt_path = resolve_extracted_image_semantic_prompt_path()
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


def get_extracted_image_semantic_prompt_snapshot() -> tuple[str, str | None]:
    prompt_path = resolve_extracted_image_semantic_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return str(prompt_path), None
    if not prompt:
        return str(prompt_path), None
    return str(prompt_path), hashlib.sha256(prompt.encode("utf-8")).hexdigest()


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


def get_extracted_image_semantic_target_model_key(target_model: str | None) -> str:
    return target_model or _DEFAULT_TARGET_MODEL_KEY


def execute_extracted_image_semantic_recognition(
    *,
    extracted_image: ExtractedImage,
    storage: MinioStorage,
    client: LlmServiceClient,
    request_id: str | None = None,
    target_model: str | None = None,
) -> ExtractedImageSemanticExecutionResult:
    content_type = _normalize_content_type(extracted_image.content_type)
    if not content_type.startswith("image/"):
        return ExtractedImageSemanticExecutionResult(
            succeeded=False,
            error_message=f"Extracted image {extracted_image.id} is not an image resource",
        )

    try:
        prompt = load_extracted_image_semantic_prompt()
    except ExtractedImageSemanticPromptError as exc:
        return ExtractedImageSemanticExecutionResult(
            succeeded=False,
            error_message=f"Semantic description prompt unavailable: {exc}",
        )

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
        return ExtractedImageSemanticExecutionResult(
            succeeded=False,
            error_message=f"Extracted image storage download failed: {exc.code}",
        )

    logger.info(
        "Executing extracted image semantic recognition image_id=%s storage_bucket=%s storage_key=%s file_size=%s model=%s request_id=%s",
        extracted_image.id,
        extracted_image.storage_bucket,
        extracted_image.storage_key,
        len(payload),
        target_model or "<default>",
        request_id,
    )
    try:
        result, error = client.chat(
            prompt=_DEFAULT_USER_PROMPT,
            system_prompt=prompt,
            model=target_model,
            request_id=request_id,
            input_parts=[
                LlmTextInputPart(text=_DEFAULT_USER_PROMPT),
                LlmImageUrlInputPart(url=_to_data_url(payload, content_type=content_type)),
            ],
        )
    except LlmChatPersistenceError as exc:
        logger.warning(
            "Semantic description persistence failed image_id=%s model=%s request_id=%s error=%s",
            extracted_image.id,
            target_model or "<default>",
            request_id,
            exc,
        )
        return ExtractedImageSemanticExecutionResult(
            succeeded=False,
            error_message=f"llm semantic description failed: {exc}",
        )

    if error is not None or result is None:
        logger.warning(
            "Semantic description llm call failed image_id=%s model=%s request_id=%s error=%s",
            extracted_image.id,
            target_model or "<default>",
            request_id,
            error,
        )
        return ExtractedImageSemanticExecutionResult(
            succeeded=False,
            error_message=f"llm semantic description failed: {error}",
        )

    return ExtractedImageSemanticExecutionResult(
        succeeded=True,
        description=result.text,
        result_model=result.model,
    )
