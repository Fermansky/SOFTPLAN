"""通用的 system prompt 加载器。

把各 agent 里几乎一字不差的样板（env 覆盖 + lru_cache + 读取 + 哈希
快照 + 自定义错误类型）抽到一处。各 agent 通过实例化 ``PromptLoader``
并把 ``load`` / ``snapshot`` / ``resolve_path`` / ``error_cls`` 重新导出
为本模块原有的公开名，从而保持对外契约不变。

设计要点：
- 每个 ``PromptLoader`` 实例都拥有**独立的** ``lru_cache``，避免不同
  agent 共用缓存导致 prompt 串位。
- ``error_cls`` 由调用方传入：保留各 agent 自身的异常类型（如
  ``TextSummaryPromptError``），方便 service 层用 ``except`` 精确捕获，
  也方便路由层做错误映射。
- ``label`` 仅用于日志，便于在多 agent 共存的进程里区分 prompt 来源。
- ``snapshot`` 故意**不走** ``lru_cache``：它每次都直接读磁盘并算
  hash，确保运维替换 prompt 文件后下一次调用能立刻拿到新指纹。
  ``load`` 才走缓存（运行期高频）。
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Callable


class PromptLoader:
    """单个 prompt 文件的加载与指纹工具。

    参数：
        default_path: 默认的 prompt 文件路径（绝对路径）。
        env_var: 覆盖默认路径的环境变量名；为 None 则不支持覆盖。
        error_cls: prompt 加载失败时抛出的异常类型（必须是 ``RuntimeError``
            的子类）。各 agent 传入自己的领域错误类型即可。
        label: 仅用于日志的人类可读标签，例如 ``"text summary"``。
    """

    def __init__(
        self,
        *,
        default_path: Path,
        env_var: str | None,
        error_cls: type[RuntimeError],
        label: str,
    ) -> None:
        self._default_path = default_path
        self._env_var = env_var
        self._error_cls = error_cls
        self._label = label
        self._logger = logging.getLogger(f"{__name__}.{label.replace(' ', '_')}")

        # 每个实例自己的 lru_cache：通过包一层闭包实现，避免类级缓存串扰。
        # 直接暴露为 ``cached_loader`` 属性，调用方可以把它当成"原汁原味"
        # 的 lru_cache 装饰函数使用（自带 ``cache_clear`` / ``cache_info``），
        # 从而保持既有 agent 的对外契约不变。
        @lru_cache(maxsize=1)
        def _cached_load() -> str:
            return self._read_prompt(raise_with=self._error_cls)

        self.cached_loader: Callable[[], str] = _cached_load

    # ---------------------------------------------------------------
    # Path resolution
    # ---------------------------------------------------------------

    def resolve_path(self) -> Path:
        """返回当前生效的 prompt 文件路径。

        如果设置了 ``env_var`` 且环境变量非空，按环境变量返回（``~`` 会
        被展开并做 ``resolve()``）；否则返回构造时传入的 ``default_path``。
        """
        if self._env_var:
            configured = os.getenv(self._env_var)
            if configured:
                return Path(configured).expanduser().resolve()
        return self._default_path

    # ---------------------------------------------------------------
    # Load (cached)
    # ---------------------------------------------------------------

    def load(self) -> str:
        """读取 prompt 文本（带进程级 lru_cache）。

        失败时抛出构造器中指定的 ``error_cls``；首次成功加载会打 info
        日志。注意：env 变量在进程启动后切换不会生效，需进程重启或调用
        ``cache_clear()``。
        """
        return self.cached_loader()

    def cache_clear(self) -> None:
        """清空 ``load()`` 的缓存（测试友好）。"""
        self.cached_loader.cache_clear()  # type: ignore[attr-defined]

    # ---------------------------------------------------------------
    # Snapshot (uncached)
    # ---------------------------------------------------------------

    def snapshot(self) -> tuple[str, str | None]:
        """返回 ``(path_str, sha256_hash | None)``。

        - 用于 StepRecord / 审计日志：每次都重新读取，能反映运维替换
          文件后的新指纹。
        - 读不到或文件为空时返回 ``(path_str, None)``，**不抛异常**——
          快照不应阻塞主流程。
        """
        prompt_path = self.resolve_path()
        try:
            prompt = prompt_path.read_text(encoding="utf-8").strip()
        except OSError:
            return str(prompt_path), None
        if not prompt:
            return str(prompt_path), None
        return str(prompt_path), hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    # ---------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------

    def _read_prompt(self, *, raise_with: type[RuntimeError]) -> str:
        prompt_path = self.resolve_path()
        try:
            prompt = prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            self._logger.warning(
                "%s prompt file is missing path=%s", self._label, prompt_path
            )
            raise raise_with(f"Prompt file not found: {prompt_path}") from exc
        except OSError as exc:
            self._logger.warning(
                "Failed to read %s prompt path=%s error=%s",
                self._label,
                prompt_path,
                exc,
            )
            raise raise_with(f"Failed to read prompt file: {prompt_path}") from exc

        if not prompt:
            self._logger.warning(
                "%s prompt file is empty path=%s", self._label, prompt_path
            )
            raise raise_with(f"Prompt file is empty: {prompt_path}")

        self._logger.info("Loaded %s prompt path=%s", self._label, prompt_path)
        return prompt
