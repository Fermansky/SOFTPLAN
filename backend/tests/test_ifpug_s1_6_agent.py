"""IFPUG 子任务 1.6（过滤关联实体）的单元测试。

形态与 s1_2 一致，详细路径已在 ``test_ifpug_s1_2_agent.py`` 覆盖，本文件
仅做核心回归。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.agents.ifpug import (
    FilterAssociativeAgentError,
    FilterAssociativePromptError,
    FilterAssociativeStep,
    IfpugContext,
    load_filter_associative_prompt,
    run_filter_associative_agent,
)
from backend.app.agents.ifpug.domain import (
    EXCLUDED_BY_ASSOCIATIVE,
    Attribute,
    DataEntity,
    Exclusion,
    SourceRef,
)
from backend.app.agents.pipeline import StepStatus
from backend.app.services import LlmChatResult, LlmUsage


_MODULE = "backend.app.agents.ifpug.steps.s1_6_filter_associative"


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
                usage=LlmUsage(prompt_tokens=7, completion_tokens=14, total_tokens=21),
                request_id=kwargs.get("request_id", "req-s1-6"),
            ),
            None,
        )


def _patch_module(client):
    return [
        patch(f"{_MODULE}.load_filter_associative_prompt", return_value="s1_6 prompt"),
        patch(
            f"{_MODULE}.get_filter_associative_prompt_snapshot",
            return_value=(
                "backend/app/prompts/ifpug_s1_6_filter_associative.txt",
                "hash-s1-6",
            ),
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
        attributes=[Attribute(name="user_id"), Attribute(name="role_id")],
        source_refs=[SourceRef(quote="q")],
    )


def _make_ctx(entities: list[DataEntity]) -> IfpugContext:
    ctx = IfpugContext(source_document="doc", session=object())
    ctx.candidate_entities.extend(entities)
    return ctx


class FilterAssociativePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_associative_prompt.cache_clear()

    def tearDown(self) -> None:
        os.environ.pop("IFPUG_S1_6_FILTER_ASSOCIATIVE_PROMPT_PATH", None)
        load_filter_associative_prompt.cache_clear()

    def test_env_override_and_empty_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("custom s1_6 prompt", encoding="utf-8")
            os.environ["IFPUG_S1_6_FILTER_ASSOCIATIVE_PROMPT_PATH"] = str(path)
            self.assertEqual(load_filter_associative_prompt(), "custom s1_6 prompt")
            load_filter_associative_prompt.cache_clear()
            path.write_text("", encoding="utf-8")
            with self.assertRaises(FilterAssociativePromptError):
                load_filter_associative_prompt()


class RunFilterAssociativeAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_associative_prompt.cache_clear()

    def tearDown(self) -> None:
        load_filter_associative_prompt.cache_clear()

    def test_empty_entities_raises(self) -> None:
        with self.assertRaises(FilterAssociativeAgentError):
            run_filter_associative_agent(entities=[], session=object())

    def test_rationale_must_be_string(self) -> None:
        client = _ClientStub(text='{"excluded": [{"id": "E001", "rationale": 42}]}')
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            with self.assertRaises(FilterAssociativeAgentError):
                run_filter_associative_agent(
                    entities=[_entity("E001")], session=object()
                )
        finally:
            _exit_all(patches)


class FilterAssociativeStepTests(unittest.TestCase):
    def setUp(self) -> None:
        load_filter_associative_prompt.cache_clear()

    def tearDown(self) -> None:
        load_filter_associative_prompt.cache_clear()

    def test_writes_exclusion_with_correct_tag(self) -> None:
        ctx = _make_ctx(
            [_entity("E001", "用户角色关系"), _entity("E002", "订单")]
        )
        client = _ClientStub(
            text='{"excluded": [{"id": "E001", "rationale": "仅含外键"}]}'
        )
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            record = FilterAssociativeStep().run(ctx)
        finally:
            _exit_all(patches)

        self.assertEqual(len(ctx.candidate_entities), 2)
        e1 = next(e for e in ctx.candidate_entities if e.id == "E001")
        self.assertEqual(e1.exclusions[0].tag, EXCLUDED_BY_ASSOCIATIVE)
        self.assertEqual(e1.exclusions[0].step, "ifpug.s1_6_filter_associative")
        self.assertEqual(record.status, StepStatus.SUCCEEDED)
        self.assertEqual(record.metrics["entities_excluded"], 1)

    def test_duplicate_id_recorded_in_warnings(self) -> None:
        ctx = _make_ctx([_entity("E001"), _entity("E002")])
        client = _ClientStub(
            text="""
            {"excluded": [
                {"id": "E001", "rationale": "r1"},
                {"id": "E001", "rationale": "r2"}
            ]}
            """,
        )
        patches = _patch_module(client)
        _enter_all(patches)
        try:
            record = FilterAssociativeStep().run(ctx)
        finally:
            _exit_all(patches)
        self.assertEqual(record.metrics["entities_excluded"], 1)
        self.assertEqual(record.metrics["warnings"]["duplicate_ids"], ["E001"])


if __name__ == "__main__":
    unittest.main()
