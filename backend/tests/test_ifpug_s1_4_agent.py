"""IFPUG 子任务 1.4（过滤代码 / 参考数据）的单元测试。

s1_4 / s1_5 / s1_6 形态与 s1_2 一致，因此每个 step 只覆盖核心 5 个路径：
- prompt env 覆盖
- JSON 字段校验
- step 写回 Exclusion 不删除元素
- 空活跃集 SKIPPED 短路
- warnings 路径
完整的"打包调用 + warnings 分类"详细测试已在 ``test_ifpug_s1_2_agent.py`` 覆盖。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.agents.ifpug import (
    FilterCodeDataAgentError,
    FilterCodeDataPromptError,
    FilterCodeDataStep,
    IfpugContext,
    load_filter_code_data_prompt,
    run_filter_code_data_agent,
)
from backend.app.agents.ifpug.domain import (
    EXCLUDED_BY_CODE_DATA,
    Attribute,
    DataEntity,
    Exclusion,
    SourceRef,
)
from backend.app.agents.pipeline import StepStatus
from backend.app.services import LlmChatResult, LlmUsage


_MODULE = "backend.app.agents.ifpug.steps.s1_4_filter_code_data"


class _ClientStub:
    def __init__(self, *, text: str):
        self.text = text
        self.config_id = None
        self.config_code = "default"
        self.last_call: dict | None = None

    def chat(self, **kwargs):
        self.last_call = kwargs
        return (
            LlmChatResult(
                text=self.text,
                model="gpt-ifpug",
                usage=LlmUsage(prompt_tokens=8, completion_tokens=16, total_tokens=24),
                request_id=kwargs.get("request_id", "req-s1-4"),
            ),
            None,
        )


def _patch_module(client):
    return [
        patch(f"{_MODULE}.load_filter_code_data_prompt", return_value="s1_4 prompt"),
        patch(
            f"{_MODULE}.get_filter_code_data_prompt_snapshot",
            return_value=("backend/app/prompts/ifpug_s1_4_filter_code_data.txt", "hash-s1-4"),
        ),
        patch(f"{_MODULE}.get_llm_service_client", return_value=client),
    ]


def _enter_all(patches):
    for p in patches:
        p.__enter__()


def _exit_all(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


def _entity(eid: str, name: str = "实体") -> DataEntity:
    return DataEntity(
        id=eid,
        name=name,
        attributes=[Attribute(name="a1")],
        source_refs=[SourceRef(quote="q")],
    )


def _make_ctx(entities: list[DataEntity]) -> IfpugContext:
    ctx = IfpugContext(source_document="doc", session=object())
    ctx.candidate_entities.extend(entities)
    return ctx


class FilterCodeDataPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_code_data_prompt.cache_clear()

    def tearDown(self) -> None:
        os.environ.pop("IFPUG_S1_4_FILTER_CODE_DATA_PROMPT_PATH", None)
        load_filter_code_data_prompt.cache_clear()

    def test_env_override_and_empty_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("custom s1_4 prompt", encoding="utf-8")
            os.environ["IFPUG_S1_4_FILTER_CODE_DATA_PROMPT_PATH"] = str(path)
            self.assertEqual(load_filter_code_data_prompt(), "custom s1_4 prompt")
            # empty
            load_filter_code_data_prompt.cache_clear()
            path.write_text("", encoding="utf-8")
            with self.assertRaises(FilterCodeDataPromptError):
                load_filter_code_data_prompt()


class RunFilterCodeDataAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_code_data_prompt.cache_clear()

    def tearDown(self) -> None:
        load_filter_code_data_prompt.cache_clear()

    def test_empty_entities_raises(self) -> None:
        with self.assertRaises(FilterCodeDataAgentError):
            run_filter_code_data_agent(entities=[], session=object())

    def test_invalid_json_raises(self) -> None:
        client = _ClientStub(text="not json")
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(FilterCodeDataAgentError):
                run_filter_code_data_agent(entities=[_entity("E001")], session=object())
        finally:
            _exit_all(patches)

    def test_missing_id_raises(self) -> None:
        client = _ClientStub(text='{"excluded": [{"rationale": "x"}]}')
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(FilterCodeDataAgentError):
                run_filter_code_data_agent(entities=[_entity("E001")], session=object())
        finally:
            _exit_all(patches)


class FilterCodeDataStepTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_code_data_prompt.cache_clear()

    def tearDown(self) -> None:
        load_filter_code_data_prompt.cache_clear()

    def test_writes_exclusion_with_correct_tag_and_step(self) -> None:
        ctx = _make_ctx([_entity("E001", "省份代码"), _entity("E002", "订单")])
        client = _ClientStub(
            text='{"excluded": [{"id": "E001", "rationale": "国标省份代码表"}]}'
        )
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            record = FilterCodeDataStep().run(ctx)
        finally:
            _exit_all(patches)

        self.assertEqual(len(ctx.candidate_entities), 2)
        e1 = next(e for e in ctx.candidate_entities if e.id == "E001")
        self.assertEqual(e1.exclusions[0].tag, EXCLUDED_BY_CODE_DATA)
        self.assertEqual(e1.exclusions[0].step, "ifpug.s1_4_filter_code_data")
        self.assertEqual(record.status, StepStatus.SUCCEEDED)
        self.assertEqual(record.metrics["entities_excluded"], 1)
        self.assertEqual(record.metrics["entities_out"], 1)

    def test_skipped_when_no_active_entities(self) -> None:
        e = _entity("E001")
        e.exclusions.append(Exclusion(tag="prev", rationale="r", step="pre"))
        ctx = _make_ctx([e])
        client = _ClientStub(text='{"excluded": []}')
        client.chat = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore
            AssertionError("LLM should not be called")
        )
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            record = FilterCodeDataStep().run(ctx)
        finally:
            _exit_all(patches)
        self.assertEqual(record.status, StepStatus.SKIPPED)


if __name__ == "__main__":
    unittest.main()
