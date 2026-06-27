"""IFPUG 子任务 1.2（过滤未维护数据）的单元测试。

覆盖：
- Prompt 加载（env 覆盖、空文件、cache_clear）
- run_filter_unmaintained_agent：入参规范化、JSON 校验、字段越界
- FilterUnmaintainedStep：写回 ctx 时只追加 Exclusion 不删除元素，
  对无活跃实体短路成 SKIPPED，正确累计 metrics 与 warnings
- Pipeline 装配：``until="s1_2"`` 能截断流水线
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.agents.ifpug import (
    FilterUnmaintainedAgentError,
    FilterUnmaintainedPromptError,
    FilterUnmaintainedStep,
    IfpugContext,
    build_logical_file_pipeline,
    list_registered_step_names,
    load_filter_unmaintained_prompt,
    run_filter_unmaintained_agent,
)
from backend.app.agents.ifpug.domain import (
    EXCLUDED_BY_UNMAINTAINED,
    Attribute,
    DataEntity,
    Exclusion,
    SourceRef,
)
from backend.app.agents.pipeline import StepStatus
from backend.app.services import LlmChatResult, LlmUsage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ClientStub:
    def __init__(self, *, text: str, config_id=None, config_code="default"):
        self.text = text
        self.config_id = config_id
        self.config_code = config_code
        self.last_call: dict | None = None

    def chat(self, **kwargs):
        self.last_call = kwargs
        return (
            LlmChatResult(
                text=self.text,
                model="gpt-ifpug",
                usage=LlmUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                request_id=kwargs.get("request_id", "req-s1-2"),
            ),
            None,
        )


def _patch_agent_module(client: _ClientStub):
    return [
        patch(
            "backend.app.agents.ifpug.steps.s1_2_filter_unmaintained.load_filter_unmaintained_prompt",
            return_value="ifpug s1_2 system prompt",
        ),
        patch(
            "backend.app.agents.ifpug.steps.s1_2_filter_unmaintained.get_filter_unmaintained_prompt_snapshot",
            return_value=(
                "backend/app/prompts/ifpug_s1_2_filter_unmaintained.txt",
                "hash-s1-2",
            ),
        ),
        patch(
            "backend.app.agents.ifpug.steps.s1_2_filter_unmaintained.get_llm_service_client",
            return_value=client,
        ),
    ]


def _enter_all(patches):
    for p in patches:
        p.__enter__()


def _exit_all(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


def _make_ctx(entities: list[DataEntity]) -> IfpugContext:
    ctx = IfpugContext(
        source_document="doc",
        counting_scope="scope",
        user_requirements="req",
        session=object(),  # sentinel；client 被 patch，不会真用到
    )
    ctx.candidate_entities.extend(entities)
    return ctx


def _entity(eid: str, name: str = "实体") -> DataEntity:
    return DataEntity(
        id=eid,
        name=name,
        description="d",
        attributes=[Attribute(name="a1")],
        source_refs=[SourceRef(quote="q", location="loc")],
    )


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


class FilterUnmaintainedPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_unmaintained_prompt.cache_clear()

    def tearDown(self) -> None:
        os.environ.pop("IFPUG_S1_2_FILTER_UNMAINTAINED_PROMPT_PATH", None)
        load_filter_unmaintained_prompt.cache_clear()

    def test_load_with_env_override(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("custom s1_2 prompt", encoding="utf-8")
            os.environ["IFPUG_S1_2_FILTER_UNMAINTAINED_PROMPT_PATH"] = str(path)
            self.assertEqual(load_filter_unmaintained_prompt(), "custom s1_2 prompt")

    def test_empty_file_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("   ", encoding="utf-8")
            os.environ["IFPUG_S1_2_FILTER_UNMAINTAINED_PROMPT_PATH"] = str(path)
            with self.assertRaises(FilterUnmaintainedPromptError):
                load_filter_unmaintained_prompt()


# ---------------------------------------------------------------------------
# Agent function: JSON parse / 字段校验
# ---------------------------------------------------------------------------


class RunFilterUnmaintainedAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_unmaintained_prompt.cache_clear()

    def tearDown(self) -> None:
        load_filter_unmaintained_prompt.cache_clear()

    def _run(self, *, text: str, entities: list[DataEntity]):
        client = _ClientStub(text=text)
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            return (
                run_filter_unmaintained_agent(
                    entities=entities,
                    counting_scope="scope",
                    user_requirements="req",
                    session=object(),
                ),
                client,
            )
        finally:
            _exit_all(patches)

    def test_empty_entities_raises(self) -> None:
        with self.assertRaises(FilterUnmaintainedAgentError):
            run_filter_unmaintained_agent(entities=[], session=object())

    def test_valid_payload_parsed_into_verdicts(self) -> None:
        text = """
        {"excluded": [
            {"id": "E001", "rationale": "由 HR 系统同步"},
            {"id": "E003", "rationale": "字典常量"}
        ]}
        """
        result, client = self._run(
            text=text,
            entities=[_entity("E001"), _entity("E002"), _entity("E003")],
        )
        self.assertEqual(len(result.verdicts), 2)
        self.assertEqual(result.verdicts[0].entity_id, "E001")
        self.assertEqual(result.verdicts[1].entity_id, "E003")
        # client 调用使用了被 patch 的 system prompt
        self.assertEqual(client.last_call["system_prompt"], "ifpug s1_2 system prompt")
        # prompt body 应该把 entity id 显式列出，便于 LLM 引用
        self.assertIn("E001", client.last_call["prompt"])
        self.assertIn("E003", client.last_call["prompt"])

    def test_invalid_top_level_raises(self) -> None:
        with self.assertRaises(FilterUnmaintainedAgentError):
            self._run(text='{"excluded": "not-a-list"}', entities=[_entity("E001")])

    def test_missing_id_raises(self) -> None:
        with self.assertRaises(FilterUnmaintainedAgentError):
            self._run(
                text='{"excluded": [{"rationale": "no id"}]}',
                entities=[_entity("E001")],
            )

    def test_rationale_must_be_string(self) -> None:
        with self.assertRaises(FilterUnmaintainedAgentError):
            self._run(
                text='{"excluded": [{"id": "E001", "rationale": 42}]}',
                entities=[_entity("E001")],
            )

    def test_empty_excluded_list_is_valid(self) -> None:
        result, _ = self._run(text='{"excluded": []}', entities=[_entity("E001")])
        self.assertEqual(result.verdicts, [])

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(FilterUnmaintainedAgentError):
            self._run(text="not json", entities=[_entity("E001")])

    def test_counting_scope_too_long_raises(self) -> None:
        with self.assertRaises(FilterUnmaintainedAgentError):
            run_filter_unmaintained_agent(
                entities=[_entity("E001")],
                counting_scope="x" * 9000,
                session=object(),
            )


# ---------------------------------------------------------------------------
# Step layer
# ---------------------------------------------------------------------------


class FilterUnmaintainedStepTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_unmaintained_prompt.cache_clear()

    def tearDown(self) -> None:
        load_filter_unmaintained_prompt.cache_clear()

    def _run_step(self, *, text: str, ctx: IfpugContext):
        client = _ClientStub(text=text)
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            return FilterUnmaintainedStep().run(ctx)
        finally:
            _exit_all(patches)

    def test_writes_exclusions_without_removing_entities(self) -> None:
        ctx = _make_ctx([_entity("E001"), _entity("E002"), _entity("E003")])
        record = self._run_step(
            text='{"excluded": [{"id": "E002", "rationale": "外部只读"}]}',
            ctx=ctx,
        )
        # 候选实体数量不变（不删除原则）
        self.assertEqual(len(ctx.candidate_entities), 3)
        # 只有 E002 被打标签
        active_ids = [e.id for e in ctx.active_entities()]
        self.assertEqual(active_ids, ["E001", "E003"])
        excluded = [e for e in ctx.candidate_entities if e.is_excluded]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0].id, "E002")
        self.assertEqual(excluded[0].exclusions[0].tag, EXCLUDED_BY_UNMAINTAINED)
        self.assertEqual(excluded[0].exclusions[0].step, "ifpug.s1_2_filter_unmaintained")
        # StepRecord metrics
        self.assertEqual(record.status, StepStatus.SUCCEEDED)
        self.assertEqual(record.metrics["entities_in"], 3)
        self.assertEqual(record.metrics["entities_excluded"], 1)
        self.assertEqual(record.metrics["entities_out"], 2)
        self.assertNotIn("warnings", record.metrics)
        # Prompt 指纹被填写
        self.assertEqual(record.prompt_hash, "hash-s1-2")
        self.assertEqual(record.usage.total_tokens, 30)

    def test_skipped_when_no_active_entities(self) -> None:
        # 所有候选都已被前置步骤排除
        e = _entity("E001")
        e.exclusions.append(
            Exclusion(tag=EXCLUDED_BY_UNMAINTAINED, rationale="prev", step="pre")
        )
        ctx = _make_ctx([e])
        # 不应发起 LLM 调用——通过给个会爆炸的 client 验证
        client = _ClientStub(text='{"excluded": []}')
        client.chat = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore
            AssertionError("LLM should not be called when active set is empty")
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            record = FilterUnmaintainedStep().run(ctx)
        finally:
            _exit_all(patches)
        self.assertEqual(record.status, StepStatus.SKIPPED)
        self.assertEqual(record.skip_reason, "no active candidate entities")
        self.assertEqual(record.metrics["entities_in"], 0)

    def test_unknown_ids_and_duplicates_go_to_warnings_but_step_succeeds(self) -> None:
        ctx = _make_ctx([_entity("E001"), _entity("E002")])
        text = """
        {"excluded": [
            {"id": "E001", "rationale": "外部数据"},
            {"id": "E001", "rationale": "duplicate"},
            {"id": "E999", "rationale": "unknown id"}
        ]}
        """
        record = self._run_step(text=text, ctx=ctx)
        self.assertEqual(record.status, StepStatus.SUCCEEDED)
        self.assertEqual(record.metrics["entities_excluded"], 1)
        warnings = record.metrics["warnings"]
        self.assertEqual(warnings["duplicate_ids"], ["E001"])
        self.assertEqual(warnings["unknown_ids"], ["E999"])

    def test_already_excluded_id_is_recorded_but_not_double_tagged(self) -> None:
        # E001 已被前置步骤排除——LLM 又把它列进 excluded 时不应再追加 Exclusion
        e1 = _entity("E001")
        e1.exclusions.append(
            Exclusion(tag="prev", rationale="r", step="pre")
        )
        ctx = _make_ctx([e1, _entity("E002")])
        record = self._run_step(
            text='{"excluded": [{"id": "E001", "rationale": "again"}]}',
            ctx=ctx,
        )
        # E001 的 exclusions 只有 1 条（来自前置步骤）
        self.assertEqual(len(e1.exclusions), 1)
        self.assertEqual(record.metrics["entities_excluded"], 0)
        self.assertIn("already_excluded_ids", record.metrics["warnings"])
        self.assertEqual(record.metrics["warnings"]["already_excluded_ids"], ["E001"])


# ---------------------------------------------------------------------------
# Pipeline 装配
# ---------------------------------------------------------------------------


class PipelineWiringTests(unittest.TestCase):
    def test_s1_2_registered_in_order(self) -> None:
        names = list_registered_step_names()
        self.assertIn("s1_2", names)
        # 顺序：s1_1 必须在 s1_2 之前
        self.assertLess(names.index("s1_1"), names.index("s1_2"))

    def test_build_pipeline_until_s1_2(self) -> None:
        pipeline = build_logical_file_pipeline(until="s1_2")
        step_names = [s.name for s in pipeline.steps]
        self.assertEqual(
            step_names,
            ["ifpug.s1_1_identify_entities", "ifpug.s1_2_filter_unmaintained"],
        )


if __name__ == "__main__":
    unittest.main()
