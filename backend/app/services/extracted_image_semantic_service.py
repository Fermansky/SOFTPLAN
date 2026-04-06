"""抽取图片语义识别执行服务。

职责：
1. 加载图片语义识别所需的提示词与默认模型配置。
2. 将已落库的 ExtractedImage 转换为 llm-service 可消费的多模态输入。
3. 执行单次语义识别，并把结果统一映射为结构化执行结果。

说明：
- 本模块只负责编排“一次执行”，不负责任务落库与状态流转。
- 当未显式指定模型时，允许 llm-service 使用其默认模型。
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
from .llm_service import LlmImageUrlInputPart, LlmServiceClient, LlmTextInputPart
from .minio_storage import MinioStorage

logger = logging.getLogger(__name__)

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extracted_image_semantic.txt"
_DEFAULT_USER_PROMPT = "请基于这张图片生成一段中文语义描述。"
_DEFAULT_TARGET_MODEL_KEY = "__LLM_SERVICE_DEFAULT__"


@dataclass(frozen=True)
class ExtractedImageSemanticExecutionResult:
    """单次语义识别执行结果。

    说明：
    - `succeeded=True` 时，`description/result_model` 可用于任务落库。
    - `succeeded=False` 时，`error_message` 保存面向任务层的失败原因。
    """

    succeeded: bool
    description: str | None = None
    result_model: str | None = None
    error_message: str | None = None


class ExtractedImageSemanticPromptError(RuntimeError):
    """图片语义提示词不可用。"""



def _normalize_content_type(content_type: str | None) -> str:
    """规范化 MIME 类型，移除参数并统一为小写。"""
    return (content_type or "").split(";")[0].strip().lower()



def _to_data_url(payload: bytes, *, content_type: str) -> str:
    """将图片字节编码为 data URL，避免直接暴露 MinIO 内部地址。"""
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{content_type};base64,{encoded}"



def resolve_extracted_image_semantic_prompt_path() -> Path:
    """解析语义识别 prompt 文件路径。"""
    configured_path = os.getenv("EXTRACTED_IMAGE_SEMANTIC_PROMPT_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def load_extracted_image_semantic_prompt() -> str:
    """加载并缓存图片语义识别系统提示词。

    失败语义：
    - 文件缺失、读取失败或内容为空时抛出 `ExtractedImageSemanticPromptError`。
    """
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
    """读取 prompt 快照信息，用于任务创建时固定路径与哈希。

    失败语义：
    - 文件不可读或为空时返回 `(path, None)`，由执行阶段再决定是否失败。
    """
    prompt_path = resolve_extracted_image_semantic_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return str(prompt_path), None
    if not prompt:
        return str(prompt_path), None
    return str(prompt_path), hashlib.sha256(prompt.encode("utf-8")).hexdigest()



def get_extracted_image_semantic_model() -> str | None:
    """读取语义识别默认模型配置。"""
    value = os.getenv("EXTRACTED_IMAGE_SEMANTIC_MODEL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def resolve_extracted_image_semantic_model(requested_model: str | None = None) -> str | None:
    """解析本次执行应使用的目标模型。

    约束：
    - 显式请求模型优先。
    - 否则回退到环境变量中的默认模型。
    - 若仍为空，则让 llm-service 使用其默认模型。
    """
    if requested_model is not None:
        stripped = requested_model.strip()
        if stripped:
            return stripped
    return get_extracted_image_semantic_model()



def get_extracted_image_semantic_target_model_key(target_model: str | None) -> str:
    """将目标模型归一化为非空去重 key。

    说明：
    - `target_model` 可以为空，表示实际执行时让 llm-service 走默认模型。
    - 任务去重层不能接受空值，因此这里使用稳定哨兵键表示该语义。
    """
    return target_model or _DEFAULT_TARGET_MODEL_KEY



def execute_extracted_image_semantic_recognition(
    *,
    extracted_image: ExtractedImage,
    storage: MinioStorage,
    client: LlmServiceClient,
    request_id: str | None = None,
    target_model: str | None = None,
) -> ExtractedImageSemanticExecutionResult:
    """执行一次图片语义识别。

    流程：
    1. 校验资源确实是图片类型。
    2. 加载系统提示词。
    3. 从 MinIO 下载图片并转换为 data URL。
    4. 调用 llm-service 多模态 chat 接口。

    副作用：
    - 会访问 MinIO。
    - 会调用 llm-service。

    失败语义：
    - 非图片、提示词不可用、存储下载失败、下游调用失败时不抛异常，统一返回失败结果。
    """
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
    result, error = client.chat(
        prompt=_DEFAULT_USER_PROMPT,
        system_prompt=prompt,
        model=target_model,
        request_id=request_id,
        input_parts=[
            # 同时保留 prompt 与文本输入块，兼容 llm-service 当前的文本和多模态入口。
            LlmTextInputPart(text=_DEFAULT_USER_PROMPT),
            LlmImageUrlInputPart(url=_to_data_url(payload, content_type=content_type)),
        ],
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
            error_message=f"llm-service semantic description failed: {error}",
        )

    return ExtractedImageSemanticExecutionResult(
        succeeded=True,
        description=result.text,
        result_model=result.model,
    )
