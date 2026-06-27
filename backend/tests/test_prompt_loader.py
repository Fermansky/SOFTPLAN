"""``agents._common.PromptLoader`` 的单元测试。

覆盖：
- env 变量覆盖默认路径
- ``load`` 的缓存语义与 ``cache_clear`` 行为
- 缺失/读失败/空文件 → 抛入参传入的 error_cls
- 多个实例之间 lru_cache 不串扰
- ``snapshot`` 不走缓存，且失败时返回 ``(path, None)`` 而不抛
"""

from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.agents._common import PromptLoader


class _LoaderErrA(RuntimeError):
    pass


class _LoaderErrB(RuntimeError):
    pass


def _make_loader(
    tmp_path: Path,
    *,
    file_name: str = "prompt.txt",
    env_var: str | None = None,
    error_cls: type[RuntimeError] = _LoaderErrA,
    label: str = "test-a",
) -> PromptLoader:
    return PromptLoader(
        default_path=tmp_path / file_name,
        env_var=env_var,
        error_cls=error_cls,
        label=label,
    )


class PromptLoaderTests(unittest.TestCase):
    def test_load_returns_stripped_content_and_caches(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "prompt.txt").write_text("  hello prompt  \n", encoding="utf-8")
            loader = _make_loader(tmp_path)

            self.assertEqual(loader.load(), "hello prompt")
            # 之后修改文件，缓存仍返回旧值
            (tmp_path / "prompt.txt").write_text("changed", encoding="utf-8")
            self.assertEqual(loader.load(), "hello prompt")
            # 清缓存后再读，拿到新值
            loader.cache_clear()
            self.assertEqual(loader.load(), "changed")

    def test_cached_loader_attribute_has_lru_cache_interface(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "prompt.txt").write_text("x", encoding="utf-8")
            loader = _make_loader(tmp_path)
            # 外部代码（含已有 agent 测试）会以 lru_cache 风格使用
            self.assertTrue(hasattr(loader.cached_loader, "cache_clear"))
            self.assertTrue(hasattr(loader.cached_loader, "cache_info"))
            self.assertEqual(loader.cached_loader(), "x")

    def test_env_var_overrides_default_path(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            default = tmp_path / "default.txt"
            override = tmp_path / "override.txt"
            default.write_text("DEFAULT", encoding="utf-8")
            override.write_text("OVERRIDE", encoding="utf-8")

            loader = PromptLoader(
                default_path=default,
                env_var="PROMPT_LOADER_TEST_PATH",
                error_cls=_LoaderErrA,
                label="env-test",
            )
            os.environ["PROMPT_LOADER_TEST_PATH"] = str(override)
            try:
                self.assertEqual(loader.resolve_path(), override.resolve())
                self.assertEqual(loader.load(), "OVERRIDE")
            finally:
                os.environ.pop("PROMPT_LOADER_TEST_PATH", None)

    def test_missing_file_raises_configured_error(self) -> None:
        with TemporaryDirectory() as tmp:
            loader = _make_loader(Path(tmp), file_name="does_not_exist.txt")
            with self.assertRaises(_LoaderErrA):
                loader.load()

    def test_empty_file_raises_configured_error(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "prompt.txt").write_text("   \n  ", encoding="utf-8")
            loader = _make_loader(tmp_path)
            with self.assertRaises(_LoaderErrA):
                loader.load()

    def test_each_instance_has_isolated_cache(self) -> None:
        # 关键回归点：保证不同 agent 共用 PromptLoader 时缓存不串扰
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
            (tmp_path / "b.txt").write_text("BBB", encoding="utf-8")
            la = _make_loader(tmp_path, file_name="a.txt", error_cls=_LoaderErrA, label="ll-a")
            lb = _make_loader(tmp_path, file_name="b.txt", error_cls=_LoaderErrB, label="ll-b")
            self.assertEqual(la.load(), "AAA")
            self.assertEqual(lb.load(), "BBB")
            la.cache_clear()
            # 清 la 不应影响 lb 的缓存
            (tmp_path / "b.txt").write_text("CHANGED", encoding="utf-8")
            self.assertEqual(lb.load(), "BBB")

    def test_snapshot_returns_path_and_hash_without_cache(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            file_path = tmp_path / "prompt.txt"
            file_path.write_text("v1", encoding="utf-8")
            loader = _make_loader(tmp_path)

            path1, hash1 = loader.snapshot()
            self.assertEqual(path1, str(file_path))
            self.assertEqual(hash1, hashlib.sha256(b"v1").hexdigest())

            # 替换文件内容，snapshot 应立即反映（与 load 缓存独立）
            file_path.write_text("v2-longer", encoding="utf-8")
            path2, hash2 = loader.snapshot()
            self.assertEqual(path2, str(file_path))
            self.assertEqual(hash2, hashlib.sha256(b"v2-longer").hexdigest())
            self.assertNotEqual(hash1, hash2)

    def test_snapshot_returns_none_hash_when_file_missing_or_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            loader = _make_loader(tmp_path, file_name="missing.txt")
            path, h = loader.snapshot()
            self.assertEqual(path, str(tmp_path / "missing.txt"))
            self.assertIsNone(h)

            empty_loader = _make_loader(tmp_path, file_name="empty.txt", label="empty-ll")
            (tmp_path / "empty.txt").write_text("   ", encoding="utf-8")
            path, h = empty_loader.snapshot()
            self.assertIsNone(h)


if __name__ == "__main__":
    unittest.main()
