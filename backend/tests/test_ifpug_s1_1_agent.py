"""IFPUG 子任务 1.1（候选数据实体识别）的单元测试（PR2）。

覆盖三层：
- 领域结构 / IfpugContext 行为
- run_identify_entities_agent：Prompt 构建、字段校验、JSON 解析
- IdentifyEntitiesStep：写回 ctx、分配稳定 id、StepRecord 元数据
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from backend.app.agents.ifpug import (
    IdentifyEntitiesAgentError,
    IdentifyEntitiesPromptError,
    IdentifyEntitiesStep,
    IfpugContext,
    build_logical_file_pipeline,
    list_registered_step_names,
    load_identify_entities_prompt,
    run_identify_entities_agent,
)
from backend.app.agents.ifpug.domain import (
    DataEntity,
    EXCLUDED_BY_UNMAINTAINED,
    Exclusion,
)
from backend.app.agents.pipeline import StepStatus
from backend.app.services import LlmChatResult, LlmUsage


# ---------------------------------------------------------------------------
# 通用 Stub
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
                usage=LlmUsage(prompt_tokens=11, completion_tokens=22, total_tokens=33),
                request_id=kwargs.get("request_id", "req-ifpug"),
            ),
            None,
        )


def _patch_agent_module(client: _ClientStub):
    return [
        patch(
            "backend.app.agents.ifpug.steps.s1_1_identify_entities.load_identify_entities_prompt",
            return_value="ifpug s1_1 system prompt",
        ),
        patch(
            "backend.app.agents.ifpug.steps.s1_1_identify_entities.get_identify_entities_prompt_snapshot",
            return_value=("backend/app/prompts/ifpug_s1_1_identify_entities.txt", "hash-s1-1"),
        ),
        patch(
            "backend.app.agents.ifpug.steps.s1_1_identify_entities.get_llm_service_client",
            return_value=client,
        ),
    ]


def _enter_all(patches):
    started = []
    for p in patches:
        started.append(p.__enter__())
    return started


def _exit_all(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 领域结构 / Context
# ---------------------------------------------------------------------------


class IfpugDomainTests(TestCase):
    def test_data_entity_is_excluded_when_any_exclusion_present(self) -> None:
        entity = DataEntity(id="E001", name="客户")
        self.assertFalse(entity.is_excluded)
        entity.exclusions.append(
            Exclusion(tag=EXCLUDED_BY_UNMAINTAINED, rationale="not maintained", step="s1_2")
        )
        self.assertTrue(entity.is_excluded)


class IfpugContextTests(TestCase):
    def test_id_generators_are_stable_and_sequential(self) -> None:
        ctx = IfpugContext(source_document="doc")
        self.assertEqual(ctx.next_entity_id(), "E001")
        self.assertEqual(ctx.next_entity_id(), "E002")
        self.assertEqual(ctx.next_logical_file_id(), "LF001")

    def test_active_entities_filters_out_excluded(self) -> None:
        ctx = IfpugContext(source_document="doc")
        kept = DataEntity(id="E001", name="客户")
        dropped = DataEntity(id="E002", name="代码字典")
        dropped.exclusions.append(
            Exclusion(tag=EXCLUDED_BY_UNMAINTAINED, rationale="r", step="s1_2")
        )
        ctx.candidate_entities.extend([kept, dropped])

        self.assertEqual([e.id for e in ctx.active_entities()], ["E001"])


# ---------------------------------------------------------------------------
# Prompt 加载
# ---------------------------------------------------------------------------


class IdentifyEntitiesPromptTests(TestCase):
    def setUp(self) -> None:
        load_identify_entities_prompt.cache_clear()
        self._temp_root = Path(os.getcwd()) / "backend" / "tests" / ".tmp"
        self._temp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        load_identify_entities_prompt.cache_clear()

    def _write_prompt_file(self, contents: str) -> Path:
        prompt_path = self._temp_root / f"ifpug-s1-1-{uuid4().hex}.txt"
        prompt_path.write_text(contents, encoding="utf-8")
        self.addCleanup(lambda: prompt_path.unlink(missing_ok=True))
        return prompt_path

    def test_load_prompt_uses_env_override(self) -> None:
        prompt_path = self._write_prompt_file("custom ifpug s1_1 prompt")
        with patch.dict(
            os.environ,
            {"IFPUG_S1_1_IDENTIFY_ENTITIES_PROMPT_PATH": str(prompt_path)},
            clear=False,
        ):
            self.assertEqual(load_identify_entities_prompt(), "custom ifpug s1_1 prompt")

    def test_load_prompt_raises_when_missing(self) -> None:
        missing = self._temp_root / "missing-ifpug-s1-1.txt"
        missing.unlink(missing_ok=True)
        with patch.dict(
            os.environ,
            {"IFPUG_S1_1_IDENTIFY_ENTITIES_PROMPT_PATH": str(missing)},
            clear=False,
        ):
            with self.assertRaises(IdentifyEntitiesPromptError):
                load_identify_entities_prompt()


# ---------------------------------------------------------------------------
# Agent 服务层
# ---------------------------------------------------------------------------


class RunIdentifyEntitiesAgentTests(TestCase):
    def tearDown(self) -> None:
        load_identify_entities_prompt.cache_clear()

    def test_rejects_blank_source_document(self) -> None:
        with self.assertRaises(IdentifyEntitiesAgentError) as cm:
            run_identify_entities_agent(source_document="   ", session=object())
        self.assertEqual(str(cm.exception), "source_document is required")

    def test_success_parses_entities_and_builds_prompt(self) -> None:
        client = _ClientStub(
            text=(
                '{"entities":['
                '{"name":"客户","description":"客户主数据。",'
                '"attributes":[{"name":"客户编号","description":"唯一标识"}],'
                '"source_refs":[{"quote":"系统应支持新增、修改、删除客户记录","location":"3.2 节"}]},'
                '{"name":"订单","description":"订单信息。",'
                '"attributes":[],"source_refs":[]}'
                ']}'
            ),
            config_id=uuid4(),
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            result = run_identify_entities_agent(
                source_document=" 文档内容 ",
                counting_scope="项目计数范围描述",
                user_requirements="用户需求描述",
                session=object(),
                model=" custom-ifpug-model ",
                request_id="req-ifpug-1",
            )
        finally:
            _exit_all(patches)

        self.assertEqual(len(result.entities), 2)
        names = [entity.name for entity in result.entities]
        self.assertEqual(names, ["客户", "订单"])
        self.assertEqual(result.entities[0].attributes[0].name, "客户编号")
        self.assertEqual(result.entities[0].source_refs[0].location, "3.2 节")
        self.assertEqual(result.model, "gpt-ifpug")
        self.assertEqual(result.usage.total_tokens, 33)
        self.assertEqual(result.prompt_path, "backend/app/prompts/ifpug_s1_1_identify_entities.txt")
        self.assertEqual(result.prompt_hash, "hash-s1-1")

        self.assertIsNotNone(client.last_call)
        self.assertEqual(client.last_call["caller_service"], "backend.agent.ifpug.s1_1_identify_entities")
        self.assertEqual(client.last_call["system_prompt"], "ifpug s1_1 system prompt")
        self.assertEqual(client.last_call["model"], "custom-ifpug-model")
        self.assertEqual(client.last_call["temperature"], 0.1)
        prompt_text = client.last_call["prompt"]
        self.assertIn("<<<COUNTING_SCOPE>>>", prompt_text)
        self.assertIn("项目计数范围描述", prompt_text)
        self.assertIn("用户需求描述", prompt_text)
        self.assertIn("文档内容", prompt_text)

    def test_dedupes_entities_with_same_name(self) -> None:
        client = _ClientStub(
            text=(
                '{"entities":['
                '{"name":"客户","description":"v1","attributes":[],"source_refs":[]},'
                '{"name":"客户","description":"v2","attributes":[],"source_refs":[]}'
                ']}'
            ),
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            result = run_identify_entities_agent(
                source_document="doc", session=object()
            )
        finally:
            _exit_all(patches)

        self.assertEqual(len(result.entities), 1)
        self.assertEqual(result.entities[0].description, "v1")

    def test_rejects_invalid_json(self) -> None:
        client = _ClientStub(text="not json")
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(IdentifyEntitiesAgentError) as cm:
                run_identify_entities_agent(source_document="doc", session=object())
        finally:
            _exit_all(patches)
        self.assertIn("invalid json", str(cm.exception))

    def test_rejects_payload_without_entities_array(self) -> None:
        client = _ClientStub(text='{"items": []}')
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(IdentifyEntitiesAgentError) as cm:
                run_identify_entities_agent(source_document="doc", session=object())
        finally:
            _exit_all(patches)
        self.assertIn("invalid field: entities", str(cm.exception))

    def test_rejects_entity_missing_name(self) -> None:
        client = _ClientStub(
            text='{"entities":[{"description":"无名","attributes":[],"source_refs":[]}]}'
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(IdentifyEntitiesAgentError) as cm:
                run_identify_entities_agent(source_document="doc", session=object())
        finally:
            _exit_all(patches)
        self.assertIn("entities[0].name", str(cm.exception))

    def test_rejects_attribute_with_invalid_type(self) -> None:
        client = _ClientStub(
            text=(
                '{"entities":[{"name":"客户","description":"",'
                '"attributes":[{"name":123,"description":""}],'
                '"source_refs":[]}]}'
            )
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(IdentifyEntitiesAgentError) as cm:
                run_identify_entities_agent(source_document="doc", session=object())
        finally:
            _exit_all(patches)
        self.assertIn("attributes[0].name", str(cm.exception))


# ---------------------------------------------------------------------------
# Step 包装
# ---------------------------------------------------------------------------


class IdentifyEntitiesStepTests(TestCase):
    def tearDown(self) -> None:
        load_identify_entities_prompt.cache_clear()

    def test_step_assigns_stable_ids_and_writes_back(self) -> None:
        client = _ClientStub(
            text=(
                '{"entities":['
                '{"name":"A","description":"","attributes":[],"source_refs":[]},'
                '{"name":"B","description":"","attributes":[],"source_refs":[]}'
                ']}'
            ),
        )
        ctx = IfpugContext(
            source_document="doc",
            counting_scope="scope",
            user_requirements="req",
            session=object(),
        )
        ctx.base.request_id = "req-step"
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            record = IdentifyEntitiesStep().run(ctx)
        finally:
            _exit_all(patches)

        self.assertEqual([e.id for e in ctx.candidate_entities], ["E001", "E002"])
        self.assertEqual(record.name, "ifpug.s1_1_identify_entities")
        self.assertEqual(record.status, StepStatus.SUCCEEDED)
        self.assertEqual(record.usage.total_tokens, 33)
        self.assertEqual(record.metrics["entities_out"], 2)
        # Step 应把 ctx 上的 request_id 透传给 LLM 客户端。
        self.assertEqual(client.last_call["request_id"], "req-step")

    def test_step_requires_session(self) -> None:
        ctx = IfpugContext(source_document="doc", session=None)
        with self.assertRaises(IdentifyEntitiesAgentError):
            IdentifyEntitiesStep().run(ctx)


# ---------------------------------------------------------------------------
# Pipeline 装配
# ---------------------------------------------------------------------------


class LogicalFilePipelineTests(TestCase):
    def tearDown(self) -> None:
        load_identify_entities_prompt.cache_clear()

    def test_s1_1_is_registered_first(self) -> None:
        # s1_1 必须是第一个注册步骤；其它步骤的存在不属于本测试关心范围。
        names = list_registered_step_names()
        self.assertGreaterEqual(len(names), 1)
        self.assertEqual(names[0], "s1_1")

    def test_until_unknown_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_logical_file_pipeline(until="s9_9")

    def test_run_pipeline_until_s1_1_populates_ctx_and_accumulates_usage(self) -> None:
        # 用 until="s1_1" 把流水线截断到只跑 s1_1，避免后续 step 调真实 LLM。
        client = _ClientStub(
            text=(
                '{"entities":['
                '{"name":"客户","description":"","attributes":[],"source_refs":[]}'
                ']}'
            ),
        )
        ctx = IfpugContext(source_document="doc", session=object())

        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            build_logical_file_pipeline(until="s1_1").run(ctx)
        finally:
            _exit_all(patches)

        self.assertEqual([e.id for e in ctx.candidate_entities], ["E001"])
        self.assertEqual(ctx.base.total_usage.total_tokens, 33)
        self.assertFalse(ctx.base.aborted)
        self.assertEqual(len(ctx.base.step_records), 1)
        self.assertEqual(ctx.base.step_records[0].name, "ifpug.s1_1_identify_entities")
