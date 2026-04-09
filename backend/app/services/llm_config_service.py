"""LLM 配置持久化服务。

职责：
1. 维护 llm_configs 的创建、更新、激活、删除与查询逻辑。
2. 统一处理配置选择规则，包括按 id 指定与回退到当前激活配置。
3. 在系统首次启动且配置表为空时，基于环境变量 bootstrap 默认配置。

说明：
- 本模块聚焦配置实体本身，不负责真正的 LLM 请求发送。
- 读接口返回脱敏后的配置视图，`api_key` 明文只保留在数据库内部与运行时解析阶段。
- 与 HTTP 状态码相关的映射由路由层负责，这里只抛出语义化异常。
"""

import logging
import os
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..database import engine
from ..models import (
    LlmConfig,
    LlmConfigCreate,
    LlmConfigListItem,
    LlmConfigProvider,
    LlmConfigRead,
    LlmConfigUpdate,
)
from ..models.common import utc_now

logger = logging.getLogger(__name__)
_DEFAULT_LLM_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_LLM_MODEL = "gpt-4o-mini"
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
_BOOTSTRAP_LLM_CODE = "default"
_BOOTSTRAP_LLM_NAME = "Default LLM Config"


class LlmConfigError(RuntimeError):
    """LLM 配置操作异常基类。"""


class LlmConfigNotFoundError(LlmConfigError):
    """指定的 LLM 配置不存在。"""


class LlmConfigDisabledError(LlmConfigError):
    """指定的 LLM 配置存在但已禁用。"""


class LlmConfigConflictError(LlmConfigError):
    """LLM 配置操作违反业务约束。"""


class LlmConfigResolutionError(LlmConfigError):
    """当前请求无法解析出可用的 LLM 配置。"""


class LlmConfigValidationError(LlmConfigError):
    """配置载荷本身不满足静态校验规则。"""


def _normalize_base_url(value: str) -> str:
    """规范化 base_url，去掉首尾空白和尾部斜杠。"""

    return value.strip().rstrip("/")


def _normalize_api_key(value: str) -> str:
    """规范化 API key，去掉首尾空白但保留中间内容。"""

    return value.strip()


def _normalize_default_model(value: str) -> str:
    """规范化默认模型名，避免仅因空白字符导致误判。"""

    return value.strip()


def validate_llm_config_values(
    *,
    base_url: str,
    api_key: str,
    default_model: str,
    timeout_seconds: float,
    require_api_key: bool,
) -> dict[str, object]:
    """执行 LLM 配置的静态校验并返回规范化后的字段值。

    该校验只关注适合入库的本地约束，不执行远端网络探测。
    `require_api_key=True` 时会把空白 key 视为非法，用于 enabled/activate 场景。
    """

    normalized_base_url = _normalize_base_url(base_url)
    if not normalized_base_url:
        raise LlmConfigValidationError("LLM api base url is required")

    parsed = urlsplit(normalized_base_url)
    if parsed.scheme not in {"http", "https"}:
        raise LlmConfigValidationError("LLM api base url must use http or https")
    if not parsed.netloc:
        raise LlmConfigValidationError("LLM api base url must include host")
    if parsed.query or parsed.fragment:
        raise LlmConfigValidationError("LLM api base url must not include query or fragment")

    normalized_default_model = _normalize_default_model(default_model)
    if not normalized_default_model:
        raise LlmConfigValidationError("LLM default model is required")

    normalized_api_key = _normalize_api_key(api_key)
    if require_api_key and not normalized_api_key:
        raise LlmConfigValidationError("LLM api key is required for enabled config")

    if timeout_seconds <= 0:
        raise LlmConfigValidationError("LLM timeout_seconds must be greater than 0")

    return {
        "base_url": normalized_base_url,
        "api_key": normalized_api_key,
        "default_model": normalized_default_model,
        "timeout_seconds": timeout_seconds,
    }


def _mask_api_key(api_key: str) -> str | None:
    """返回仅用于展示的脱敏 API key。"""

    stripped = api_key.strip()
    if not stripped:
        return None
    if len(stripped) <= 4:
        return "*" * len(stripped)
    if len(stripped) <= 8:
        return f"{stripped[:2]}{'*' * (len(stripped) - 4)}{stripped[-2:]}"
    return f"{stripped[:4]}{'*' * (len(stripped) - 8)}{stripped[-4:]}"


def serialize_llm_config(config: LlmConfig) -> LlmConfigRead:
    """把持久化配置实体转换为对外读取模型。"""

    return LlmConfigRead(
        id=config.id,
        code=config.code,
        name=config.name,
        provider=config.provider,
        base_url=config.base_url,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
        is_active=config.is_active,
        enabled=config.enabled,
        has_api_key=bool(config.api_key.strip()),
        api_key_masked=_mask_api_key(config.api_key),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def serialize_llm_config_list_item(config: LlmConfig) -> LlmConfigListItem:
    """把完整读取模型收窄为列表项视图。"""

    return LlmConfigListItem.model_validate(serialize_llm_config(config))


def _get_existing_llm_config(session: Session, config_id: UUID) -> LlmConfig | None:
    """读取未软删除的指定配置，不存在时返回 `None`。"""

    statement = select(LlmConfig).where(LlmConfig.id == config_id, LlmConfig.deleted_at.is_(None))
    return session.exec(statement).first()


def get_llm_config_or_raise(session: Session, config_id: UUID) -> LlmConfig:
    """读取指定配置，不存在时抛出语义化异常。"""

    config = _get_existing_llm_config(session, config_id)
    if config is None:
        raise LlmConfigNotFoundError("LLM config not found")
    return config


def list_llm_configs(session: Session) -> list[LlmConfig]:
    """列出所有未软删除配置，优先展示激活项和新近项。"""

    statement = (
        select(LlmConfig)
        .where(LlmConfig.deleted_at.is_(None))
        .order_by(LlmConfig.is_active.desc(), LlmConfig.created_at.desc())
    )
    return list(session.exec(statement).all())


def get_active_llm_config(session: Session) -> LlmConfig | None:
    """返回当前激活配置；若不存在则返回 `None`。"""

    statement = select(LlmConfig).where(
        LlmConfig.deleted_at.is_(None),
        LlmConfig.is_active.is_(True),
    )
    return session.exec(statement).first()


def _deactivate_other_configs(session: Session, *, exclude_id: UUID | None = None) -> None:
    """把其他激活配置全部取消激活，可选保留一个指定配置。"""

    statement = select(LlmConfig).where(
        LlmConfig.deleted_at.is_(None),
        LlmConfig.is_active.is_(True),
    )
    for config in session.exec(statement).all():
        if exclude_id is not None and config.id == exclude_id:
            continue
        config.is_active = False
        config.updated_at = utc_now()
        session.add(config)


def _commit_and_refresh(session: Session, config: LlmConfig) -> LlmConfig:
    """提交配置变更并刷新实体，唯一键冲突时转为业务异常。"""

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise LlmConfigConflictError("LLM config code already exists") from exc
    session.refresh(config)
    return config

def _build_probe_failure_message(result) -> str:
    """把结构化探针结果折叠为适合冲突异常返回的摘要文本。"""

    if result.error_message:
        return f"LLM config validation failed at {result.stage}: {result.error_message}"
    return f"LLM config validation failed at {result.stage}"


def _validate_activation_candidate(
    *,
    config_id: UUID | None,
    config_code: str | None,
    base_url: str,
    api_key: str,
    default_model: str,
    timeout_seconds: float,
) -> None:
    """对候选激活配置执行 strict 探针，失败时抛出业务冲突异常。"""

    from .llm_service import LlmConfigValidationDepth, LlmServiceConfig, validate_llm_service_config

    result = validate_llm_service_config(
        LlmServiceConfig(
            config_id=config_id,
            config_code=config_code,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
        ),
        depth=LlmConfigValidationDepth.strict,
    )
    if not result.valid:
        raise LlmConfigConflictError(_build_probe_failure_message(result))


def create_llm_config(session: Session, payload: LlmConfigCreate) -> LlmConfig:
    """创建 LLM 配置，并在“创建即激活”时先通过严格探针。"""

    if payload.is_active and not payload.enabled:
        raise LlmConfigValidationError("Active LLM config must be enabled")

    validated = validate_llm_config_values(
        base_url=payload.base_url,
        api_key=payload.api_key,
        default_model=payload.default_model,
        timeout_seconds=payload.timeout_seconds,
        require_api_key=payload.enabled,
    )

    if payload.is_active:
        _validate_activation_candidate(
            config_id=None,
            config_code=payload.code,
            base_url=validated["base_url"],
            api_key=validated["api_key"],
            default_model=validated["default_model"],
            timeout_seconds=validated["timeout_seconds"],
        )

    now = utc_now()
    config = LlmConfig(
        code=payload.code,
        name=payload.name,
        provider=payload.provider,
        base_url=validated["base_url"],
        api_key=validated["api_key"],
        default_model=validated["default_model"],
        timeout_seconds=validated["timeout_seconds"],
        is_active=payload.is_active,
        enabled=payload.enabled,
        created_at=now,
        updated_at=now,
    )
    if config.is_active:
        _deactivate_other_configs(session)
    session.add(config)
    return _commit_and_refresh(session, config)


def update_llm_config(session: Session, config_id: UUID, payload: LlmConfigUpdate) -> LlmConfig:
    """更新 LLM 配置并重新应用静态校验与字段规范化。"""

    config = get_llm_config_or_raise(session, config_id)
    update_data = payload.model_dump(exclude_unset=True)

    if update_data.get("enabled") is False and config.is_active:
        raise LlmConfigConflictError("Active LLM config cannot be disabled")

    merged_enabled = update_data.get("enabled", config.enabled)
    merged_base_url = update_data.get("base_url", config.base_url)
    merged_api_key = update_data.get("api_key", config.api_key)
    merged_default_model = update_data.get("default_model", config.default_model)
    merged_timeout_seconds = update_data.get("timeout_seconds", config.timeout_seconds)

    validated = validate_llm_config_values(
        base_url=merged_base_url,
        api_key=merged_api_key,
        default_model=merged_default_model,
        timeout_seconds=merged_timeout_seconds,
        require_api_key=merged_enabled,
    )

    if "name" in update_data and update_data["name"] is not None:
        config.name = update_data["name"]
    if "provider" in update_data and update_data["provider"] is not None:
        config.provider = update_data["provider"]

    config.base_url = validated["base_url"]
    config.api_key = validated["api_key"]
    config.default_model = validated["default_model"]
    config.timeout_seconds = validated["timeout_seconds"]
    config.enabled = merged_enabled
    config.updated_at = utc_now()
    session.add(config)
    return _commit_and_refresh(session, config)


def activate_llm_config(session: Session, config_id: UUID) -> LlmConfig:
    """激活指定配置，并要求其先通过 strict 探针校验。"""

    config = get_llm_config_or_raise(session, config_id)
    if not config.enabled:
        raise LlmConfigDisabledError("LLM config is disabled")

    validated = validate_llm_config_values(
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
        require_api_key=True,
    )
    _validate_activation_candidate(
        config_id=config.id,
        config_code=config.code,
        base_url=validated["base_url"],
        api_key=validated["api_key"],
        default_model=validated["default_model"],
        timeout_seconds=validated["timeout_seconds"],
    )

    _deactivate_other_configs(session, exclude_id=config.id)
    config.is_active = True
    config.updated_at = utc_now()
    session.add(config)
    return _commit_and_refresh(session, config)


def delete_llm_config(session: Session, config_id: UUID) -> None:
    """软删除指定配置；当前激活配置不允许删除。"""

    config = get_llm_config_or_raise(session, config_id)
    if config.is_active:
        raise LlmConfigConflictError("Active LLM config cannot be deleted")

    now = utc_now()
    config.deleted_at = now
    config.updated_at = now
    session.add(config)
    session.commit()


def resolve_llm_config(session: Session, *, config_id: UUID | None = None) -> LlmConfig:
    """解析请求应使用的配置，优先显式 id，其次当前激活配置。"""

    if config_id is not None:
        config = get_llm_config_or_raise(session, config_id)
        if not config.enabled:
            raise LlmConfigDisabledError("LLM config is disabled")
        return config

    config = get_active_llm_config(session)
    if config is None:
        raise LlmConfigResolutionError("No active LLM config is configured")
    if not config.enabled:
        raise LlmConfigDisabledError("LLM config is disabled")
    return config


def bootstrap_llm_configs_from_env() -> None:
    """基于环境变量初始化默认配置，缺失 key 时仅创建禁用草稿。"""

    with Session(engine) as session:
        existing = session.exec(select(LlmConfig.id).limit(1)).first()
        if existing is not None:
            return

        raw_api_key = os.getenv("LLM_API_KEY", "")
        enabled = bool(raw_api_key.strip())
        try:
            timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", str(_DEFAULT_LLM_TIMEOUT_SECONDS)))
            validated = validate_llm_config_values(
                base_url=os.getenv("LLM_API_BASE_URL", _DEFAULT_LLM_BASE_URL),
                api_key=raw_api_key,
                default_model=os.getenv("LLM_DEFAULT_MODEL", _DEFAULT_LLM_MODEL),
                timeout_seconds=timeout_seconds,
                require_api_key=enabled,
            )
        except (TypeError, ValueError, LlmConfigValidationError) as exc:
            logger.warning("Skipped bootstrapping default llm config from environment: %s", exc)
            return

        now = utc_now()
        config = LlmConfig(
            code=_BOOTSTRAP_LLM_CODE,
            name=_BOOTSTRAP_LLM_NAME,
            provider=LlmConfigProvider.openai_compatible,
            base_url=validated["base_url"],
            api_key=validated["api_key"],
            default_model=validated["default_model"],
            timeout_seconds=validated["timeout_seconds"],
            is_active=enabled,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        session.add(config)
        session.commit()
        if enabled:
            logger.info("Bootstrapped default llm config from environment")
        else:
            logger.warning("Bootstrapped default llm config as disabled draft because LLM_API_KEY is missing")
