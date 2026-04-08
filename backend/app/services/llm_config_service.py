"""LLM 配置持久化服务。

职责：
1. 维护 llm_configs 的创建、更新、激活、删除与查询逻辑。
2. 统一处理配置选择规则，包括“按 id 指定”与“回退当前激活配置”。
3. 在系统首次启动且配置表为空时，基于环境变量 bootstrap 默认配置。

说明：
- 本模块聚焦配置实体本身，不负责真实 LLM 请求发送。
- 读接口返回脱敏后的配置视图，`api_key` 明文只保留在数据库内部与运行时解析阶段。
- 与 HTTP 状态码相关的映射由路由层负责，这里只抛出语义化异常。
"""
import logging
import os
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
    """配置载荷本身不满足校验规则。"""


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def _mask_api_key(api_key: str) -> str | None:
    stripped = api_key.strip()
    if not stripped:
        return None
    if len(stripped) <= 4:
        return "*" * len(stripped)
    if len(stripped) <= 8:
        return f"{stripped[:2]}{'*' * (len(stripped) - 4)}{stripped[-2:]}"
    return f"{stripped[:4]}{'*' * (len(stripped) - 8)}{stripped[-4:]}"


def serialize_llm_config(config: LlmConfig) -> LlmConfigRead:
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
    return LlmConfigListItem.model_validate(serialize_llm_config(config))


def _get_existing_llm_config(session: Session, config_id: UUID) -> LlmConfig | None:
    statement = select(LlmConfig).where(LlmConfig.id == config_id, LlmConfig.deleted_at.is_(None))
    return session.exec(statement).first()


def get_llm_config_or_raise(session: Session, config_id: UUID) -> LlmConfig:
    config = _get_existing_llm_config(session, config_id)
    if config is None:
        raise LlmConfigNotFoundError("LLM config not found")
    return config


def list_llm_configs(session: Session) -> list[LlmConfig]:
    statement = (
        select(LlmConfig)
        .where(LlmConfig.deleted_at.is_(None))
        .order_by(LlmConfig.is_active.desc(), LlmConfig.created_at.desc())
    )
    return list(session.exec(statement).all())


def get_active_llm_config(session: Session) -> LlmConfig | None:
    statement = select(LlmConfig).where(
        LlmConfig.deleted_at.is_(None),
        LlmConfig.is_active.is_(True),
    )
    return session.exec(statement).first()


def _deactivate_other_configs(session: Session, *, exclude_id: UUID | None = None) -> None:
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
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise LlmConfigConflictError("LLM config code already exists") from exc
    session.refresh(config)
    return config


def create_llm_config(session: Session, payload: LlmConfigCreate) -> LlmConfig:
    if payload.is_active and not payload.enabled:
        raise LlmConfigValidationError("Active LLM config must be enabled")

    now = utc_now()
    config = LlmConfig(
        code=payload.code,
        name=payload.name,
        provider=payload.provider,
        base_url=_normalize_base_url(payload.base_url),
        api_key=payload.api_key,
        default_model=payload.default_model,
        timeout_seconds=payload.timeout_seconds,
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
    config = get_llm_config_or_raise(session, config_id)
    update_data = payload.model_dump(exclude_unset=True)

    if update_data.get("enabled") is False and config.is_active:
        raise LlmConfigConflictError("Active LLM config cannot be disabled")

    for field_name, value in update_data.items():
        if value is None:
            continue
        if field_name == "base_url":
            value = _normalize_base_url(value)
        setattr(config, field_name, value)

    config.updated_at = utc_now()
    session.add(config)
    return _commit_and_refresh(session, config)


def activate_llm_config(session: Session, config_id: UUID) -> LlmConfig:
    config = get_llm_config_or_raise(session, config_id)
    if not config.enabled:
        raise LlmConfigDisabledError("LLM config is disabled")

    _deactivate_other_configs(session, exclude_id=config.id)
    config.is_active = True
    config.updated_at = utc_now()
    session.add(config)
    return _commit_and_refresh(session, config)


def delete_llm_config(session: Session, config_id: UUID) -> None:
    config = get_llm_config_or_raise(session, config_id)
    if config.is_active:
        raise LlmConfigConflictError("Active LLM config cannot be deleted")

    now = utc_now()
    config.deleted_at = now
    config.updated_at = now
    session.add(config)
    session.commit()


def resolve_llm_config(session: Session, *, config_id: UUID | None = None) -> LlmConfig:
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
    with Session(engine) as session:
        existing = session.exec(select(LlmConfig.id).limit(1)).first()
        if existing is not None:
            return

        now = utc_now()
        config = LlmConfig(
            code=_BOOTSTRAP_LLM_CODE,
            name=_BOOTSTRAP_LLM_NAME,
            provider=LlmConfigProvider.openai_compatible,
            base_url=_normalize_base_url(os.getenv("LLM_API_BASE_URL", _DEFAULT_LLM_BASE_URL)),
            api_key=os.getenv("LLM_API_KEY", ""),
            default_model=os.getenv("LLM_DEFAULT_MODEL", _DEFAULT_LLM_MODEL),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", str(_DEFAULT_LLM_TIMEOUT_SECONDS))),
            is_active=True,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        session.add(config)
        session.commit()
        logger.info("Bootstrapped default llm config from environment")


