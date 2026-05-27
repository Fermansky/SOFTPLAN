# 如何添加新 Agent

本文档描述在 `backend/app/agents/` 下添加一个新 Agent 的完整步骤和约定，供 AI 编写代码时遵循。

---

## 目录结构

每个 Agent 是 `backend/app/agents/` 下的独立子目录：

```
backend/app/agents/
└── your_agent_name/
    ├── __init__.py        # 导出 service 的公共符号
    ├── service.py         # Agent 调用逻辑
    └── prompting.py       # Prompt 文件加载
```

---

## Step 1：创建 Prompt 文件

在 `backend/app/prompts/` 下创建 `.txt` 文件：

```
backend/app/prompts/your_agent_name_agent.txt
```

文件内容是 System Prompt 的完整文本。不要在代码里内联 Prompt 字符串。

---

## Step 2：实现 prompting.py

参考 `document_structuring/prompting.py` 的模式：

```python
"""Prompt loading helpers for the <your_agent_name> agent."""

import hashlib
import os
from functools import lru_cache
from pathlib import Path

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "your_agent_name_agent.txt"

# 定义 Prompt 加载失败时抛出的错误
class YourAgentNamePromptError(RuntimeError):
    """Raised when the <your_agent_name> prompt is unavailable."""

def resolve_prompt_path() -> Path:
    configured = os.getenv("YOUR_AGENT_NAME_AGENT_PROMPT_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return _DEFAULT_PROMPT_PATH

@lru_cache(maxsize=1)
def load_prompt() -> str:
    # 读取并缓存 Prompt 文件，文件不存在或为空时抛 YourAgentNamePromptError
    ...

def get_prompt_snapshot() -> tuple[str, str | None]:
    # 返回 (prompt_path_str, sha256_hash_or_none)
    ...
```

---

## Step 3：实现 service.py

```python
"""Single-run <your_agent_name> agent service."""

from dataclasses import dataclass
from uuid import UUID
from sqlmodel import Session

from ...services import LlmChatPersistenceError, LlmConfigError, LlmUsage, get_llm_service_client
from .prompting import YourAgentNamePromptError, get_prompt_snapshot, load_prompt

_CALLER_SERVICE_NAME = "backend.agent.your_agent_name"
_DEFAULT_TEMPERATURE = 0.1

@dataclass(frozen=True)
class YourAgentNameResult:
    # 定义输出字段
    model: str
    request_id: str | None
    usage: LlmUsage
    effective_config_id: UUID | None
    effective_config_code: str | None
    prompt_path: str
    prompt_hash: str | None

class YourAgentNameError(RuntimeError):
    """Raised when the agent cannot complete."""

def run_your_agent_name(
    *,
    source_text: str,
    session: Session,
    config_id: UUID | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    request_id: str | None = None,
) -> YourAgentNameResult:
    system_prompt = load_prompt()
    prompt_path, prompt_hash = get_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    result, error = client.chat(
        prompt=build_user_prompt(source_text),
        system_prompt=system_prompt,
        model=model,
        temperature=_DEFAULT_TEMPERATURE if temperature is None else temperature,
        max_tokens=max_tokens,
        request_id=request_id,
        caller_service=_CALLER_SERVICE_NAME,
    )

    if error is not None or result is None:
        raise YourAgentNameError(error or "agent failed")

    # 处理 result.text，返回 YourAgentNameResult
    ...
```

**注意**：
- `LlmChatPersistenceError`、`LlmConfigError`、`YourAgentNamePromptError` 不要在 service 内 catch，让上层路由处理
- 不要在 service 内写业务数据库表，Agent 只返回结果

---

## Step 4：更新 __init__.py

```python
from .service import YourAgentNameError, YourAgentNameResult, run_your_agent_name
from .prompting import YourAgentNamePromptError

__all__ = [
    "YourAgentNameError",
    "YourAgentNamePromptError",
    "YourAgentNameResult",
    "run_your_agent_name",
]
```

---

## Step 5：添加路由（如需要）

在 `backend/app/api/routers/agents.py` 中添加新端点。参考已有的 `debug_run_document_structuring_agent` 端点。

错误映射约定：
- `LlmConfigNotFoundError` → 404
- `LlmConfigDisabledError` / `LlmConfigConflictError` → 409
- `LlmConfigResolutionError` → 503
- `YourAgentNamePromptError` / `LlmChatPersistenceError` → 500
- `YourAgentNameError`（source_text 相关）→ 422；其他 → 502

---

## Step 6：更新文档

在 `docs/current/agents.md` 中为新 Agent 添加一个条目，按照已有格式描述触发时机、输入输出、Prompt 策略。
