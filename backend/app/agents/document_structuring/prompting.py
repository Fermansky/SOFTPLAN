"""document structuring agent 的 prompt 加载辅助。

实际加载逻辑统一在 ``agents._common.prompt_loader.PromptLoader`` 中实现；
本模块只做"实例化 + 把方法导出为既有公开名"，保证对外契约（函数名、
异常类）不变。
"""

from __future__ import annotations

from pathlib import Path

from .._common import PromptLoader

_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "document_structuring_agent.txt"
)
_ENV_VAR = "DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH"


class DocumentStructuringPromptError(RuntimeError):
    """document structuring prompt 不可用时抛出。"""


_loader = PromptLoader(
    default_path=_DEFAULT_PROMPT_PATH,
    env_var=_ENV_VAR,
    error_cls=DocumentStructuringPromptError,
    label="document structuring",
)


def resolve_document_structuring_prompt_path() -> Path:
    return _loader.resolve_path()


# 直接绑定 lru_cache 装饰的可调用对象，从而保留 ``.cache_clear()`` /
# ``.cache_info()`` 接口，测试与既有代码完全无感。
load_document_structuring_prompt = _loader.cached_loader


def get_document_structuring_prompt_snapshot() -> tuple[str, str | None]:
    return _loader.snapshot()
