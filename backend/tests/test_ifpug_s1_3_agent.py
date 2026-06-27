"""IFPUG 子任务 1.3（合并同义实体）的单元测试。

覆盖：
- Prompt 加载（env 覆盖、空文件、cache_clear）
- run_merge_duplicates_agent：JSON 校验、字段校验
- MergeDuplicatesStep：
    * canonical 选最小 id；被合并实体打 EXCLUDED_BY_DUPLICATE
    * 属性 / source_refs 去重并入 canonical
    * 写 EntityRelation(duplicate_of)
    * 传递闭包：``{E1,E2}`` 与 ``{E2,E3}`` 合并成 ``{E1,E2,E3}``
    * 未知 id / 非活跃 id 进入 warnings 但 step 仍 SUCCEEDED
    * 活跃实体 < 2 时短路 SKIPPED
- Pipeline 装配：``until="s1_3"`` 能截断流水线
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app.agents.ifpug import (
    IfpugContext,
    MergeDuplicatesAgentError,
    MergeDuplicatesPromptError,
    MergeDuplicatesStep,
    build_logical_file_pipeline,
    list_registered_step_names,
    load_merge_duplicates_prompt,
    run_merge_duplicates_agent,
)
from backend.app.agents.ifpug.domain import (
    EXCLUDED_BY_DUPLICATE,
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
                usage=LlmUsage(prompt_tokens=12, completion_tokens=24, total_tokens=36),
                request_id=kwargs.get("request_id", "req-s1-3"),
            ),
            None,
        )


def _patch_agent_module(client: _ClientStub):
    return [
        patch(
            "backend.app.agents.ifpug.steps.s1_3_merge_duplicates.load_merge_duplicates_prompt",
            return_value="ifpug s1_3 system prompt",
        ),
        patch(
            "backend.app.agents.ifpug.steps.s1_3_merge_duplicates.get_merge_duplicates_prompt_snapshot",
            return_value=(
                "backend/app/prompts/ifpug_s1_3_merge_duplicates.txt",
                "hash-s1-3",
            ),
        ),
        patch(
            "backend.app.agents.ifpug.steps.s1_3_merge_duplicates.get_llm_service_client",
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
        session=object(),
    )
    ctx.candidate_entities.extend(entities)
    return ctx


def _entity(
    eid: str,
    name: str,
    *,
    attrs: list[str] | None = None,
    quotes: list[tuple[str, str | None]] | None = None,
) -> DataEntity:
    return DataEntity(
        id=eid,
        name=name,
        description=f"{name} 描述",
        attributes=[Attribute(name=a) for a in (attrs or [])],
        source_refs=[SourceRef(quote=q, location=loc) for q, loc in (quotes or [])],
    )


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


class MergeDuplicatesPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        load_merge_duplicates_prompt.cache_clear()

    def tearDown(self) -> None:
        os.environ.pop("IFPUG_S1_3_MERGE_DUPLICATES_PROMPT_PATH", None)
        load_merge_duplicates_prompt.cache_clear()

    def test_load_with_env_override(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("custom s1_3 prompt", encoding="utf-8")
            os.environ["IFPUG_S1_3_MERGE_DUPLICATES_PROMPT_PATH"] = str(path)
            self.assertEqual(load_merge_duplicates_prompt(), "custom s1_3 prompt")

    def test_empty_file_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.txt"
            path.write_text("", encoding="utf-8")
            os.environ["IFPUG_S1_3_MERGE_DUPLICATES_PROMPT_PATH"] = str(path)
            with self.assertRaises(MergeDuplicatesPromptError):
                load_merge_duplicates_prompt()


# ---------------------------------------------------------------------------
# Agent function
# ---------------------------------------------------------------------------


class RunMergeDuplicatesAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        load_merge_duplicates_prompt.cache_clear()

    def tearDown(self) -> None:
        load_merge_duplicates_prompt.cache_clear()

    def _run(self, *, text: str, entities: list[DataEntity]):
        client = _ClientStub(text=text)
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            return (
                run_merge_duplicates_agent(
                    entities=entities,
                    counting_scope="scope",
                    user_requirements="req",
                    session=object(),
                ),
                client,
            )
        finally:
            _exit_all(patches)

    def test_fewer_than_two_entities_raises(self) -> None:
        with self.assertRaises(MergeDuplicatesAgentError):
            run_merge_duplicates_agent(entities=[_entity("E001", "客户")], session=object())

    def test_valid_payload(self) -> None:
        text = """
        {"groups": [
            {"members": ["E001", "E003"], "canonical_name": "客户", "rationale": "同义"}
        ]}
        """
        result, _ = self._run(
            text=text,
            entities=[_entity("E001", "客户"), _entity("E002", "订单"), _entity("E003", "顾客")],
        )
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.groups[0].members, ("E001", "E003"))
        self.assertEqual(result.groups[0].canonical_name, "客户")

    def test_degenerate_group_with_single_member_raises(self) -> None:
        with self.assertRaises(MergeDuplicatesAgentError):
            self._run(
                text='{"groups": [{"members": ["E001"], "canonical_name": "x", "rationale": "r"}]}',
                entities=[_entity("E001", "a"), _entity("E002", "b")],
            )

    def test_duplicate_members_within_group_are_deduplicated(self) -> None:
        text = """
        {"groups": [
            {"members": ["E001", "E001", "E002"], "canonical_name": "客户", "rationale": "同义"}
        ]}
        """
        result, _ = self._run(
            text=text,
            entities=[_entity("E001", "a"), _entity("E002", "b")],
        )
        # 去重后仍 >=2，合法
        self.assertEqual(result.groups[0].members, ("E001", "E002"))

    def test_duplicates_only_become_degenerate_raises(self) -> None:
        # 去重后只剩 1 个成员 → invalid
        text = """
        {"groups": [
            {"members": ["E001", "E001"], "canonical_name": "x", "rationale": "r"}
        ]}
        """
        with self.assertRaises(MergeDuplicatesAgentError):
            self._run(
                text=text,
                entities=[_entity("E001", "a"), _entity("E002", "b")],
            )

    def test_empty_groups_is_valid(self) -> None:
        result, _ = self._run(
            text='{"groups": []}',
            entities=[_entity("E001", "a"), _entity("E002", "b")],
        )
        self.assertEqual(result.groups, [])

    def test_invalid_top_level_raises(self) -> None:
        with self.assertRaises(MergeDuplicatesAgentError):
            self._run(
                text='{"groups": "not-a-list"}',
                entities=[_entity("E001", "a"), _entity("E002", "b")],
            )


# ---------------------------------------------------------------------------
# Step layer
# ---------------------------------------------------------------------------


class MergeDuplicatesStepTests(unittest.TestCase):
    def setUp(self) -> None:
        load_merge_duplicates_prompt.cache_clear()

    def tearDown(self) -> None:
        load_merge_duplicates_prompt.cache_clear()

    def test_canonical_is_min_id_and_others_get_excluded(self) -> None:
        ctx = _make_ctx(
            [
                _entity("E001", "客户", attrs=["name", "phone"]),
                _entity("E002", "订单", attrs=["order_id"]),
                _entity("E003", "顾客", attrs=["name", "address"]),
            ]
        )
        client = _ClientStub(
            text='{"groups": [{"members": ["E001", "E003"], "canonical_name": "客户", "rationale": "同义"}]}',
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            record = MergeDuplicatesStep().run(ctx)
        finally:
            _exit_all(patches)

        # 候选实体不被删除
        self.assertEqual(len(ctx.candidate_entities), 3)
        # 活跃实体：E001（canonical）+ E002
        active_ids = [e.id for e in ctx.active_entities()]
        self.assertEqual(active_ids, ["E001", "E002"])
        # E003 被打 EXCLUDED_BY_DUPLICATE
        e003 = next(e for e in ctx.candidate_entities if e.id == "E003")
        self.assertTrue(e003.is_excluded)
        self.assertEqual(e003.exclusions[0].tag, EXCLUDED_BY_DUPLICATE)
        self.assertIn("merged into E001", e003.exclusions[0].rationale)
        # canonical 的属性合并（name 已有 → 不重复添加；address 是新的 → 添加）
        e001 = next(e for e in ctx.candidate_entities if e.id == "E001")
        attr_names = [a.name for a in e001.attributes]
        self.assertEqual(attr_names, ["name", "phone", "address"])
        # 写了 EntityRelation
        self.assertEqual(len(ctx.relations), 1)
        rel = ctx.relations[0]
        self.assertEqual(rel.from_id, "E003")
        self.assertEqual(rel.to_id, "E001")
        self.assertEqual(rel.relation_type, "duplicate_of")
        # metrics
        self.assertEqual(record.status, StepStatus.SUCCEEDED)
        self.assertEqual(record.metrics["entities_in"], 3)
        self.assertEqual(record.metrics["groups_proposed"], 1)
        self.assertEqual(record.metrics["groups_applied"], 1)
        self.assertEqual(record.metrics["entities_merged"], 1)
        self.assertEqual(record.metrics["entities_out"], 2)
        self.assertEqual(record.metrics["canonical_ids"], ["E001"])
        self.assertNotIn("warnings", record.metrics)
        # prompt 指纹
        self.assertEqual(record.prompt_hash, "hash-s1-3")

    def test_transitive_closure_merges_chained_groups(self) -> None:
        # LLM 给两个组：{E001,E002} 与 {E002,E003}；并查集应合并成 {E001,E002,E003}
        ctx = _make_ctx(
            [
                _entity("E001", "a", attrs=["x"]),
                _entity("E002", "b", attrs=["y"]),
                _entity("E003", "c", attrs=["z"]),
            ]
        )
        text = """
        {"groups": [
            {"members": ["E001", "E002"], "canonical_name": "AB", "rationale": "ab"},
            {"members": ["E002", "E003"], "canonical_name": "BC", "rationale": "bc"}
        ]}
        """
        client = _ClientStub(text=text)
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            record = MergeDuplicatesStep().run(ctx)
        finally:
            _exit_all(patches)

        active_ids = [e.id for e in ctx.active_entities()]
        self.assertEqual(active_ids, ["E001"])
        # E002、E003 都被合并到 E001
        e002 = next(e for e in ctx.candidate_entities if e.id == "E002")
        e003 = next(e for e in ctx.candidate_entities if e.id == "E003")
        self.assertTrue(e002.is_excluded)
        self.assertTrue(e003.is_excluded)
        # canonical 上属性合并：x + y + z
        e001 = next(e for e in ctx.candidate_entities if e.id == "E001")
        self.assertEqual([a.name for a in e001.attributes], ["x", "y", "z"])
        # 2 条关系：E002→E001，E003→E001
        rel_pairs = sorted((r.from_id, r.to_id) for r in ctx.relations)
        self.assertEqual(rel_pairs, [("E002", "E001"), ("E003", "E001")])
        # rationale 累加：两个组的原因都应出现在 canonical Exclusion 里
        self.assertIn("ab", e002.exclusions[0].rationale)
        self.assertEqual(record.metrics["groups_applied"], 1)  # 闭包后只剩 1 个有效合并组
        self.assertEqual(record.metrics["entities_merged"], 2)

    def test_source_refs_dedupe_on_merge(self) -> None:
        ctx = _make_ctx(
            [
                _entity(
                    "E001",
                    "a",
                    quotes=[("q1", "loc1"), ("q2", "loc2")],
                ),
                _entity(
                    "E002",
                    "b",
                    # q1+loc1 重复（应去重），q3+loc3 新增
                    quotes=[("q1", "loc1"), ("q3", "loc3")],
                ),
            ]
        )
        client = _ClientStub(
            text='{"groups": [{"members": ["E001", "E002"], "canonical_name": "ab", "rationale": "r"}]}',
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            MergeDuplicatesStep().run(ctx)
        finally:
            _exit_all(patches)

        e001 = next(e for e in ctx.candidate_entities if e.id == "E001")
        quote_keys = [(r.quote, r.location) for r in e001.source_refs]
        self.assertEqual(quote_keys, [("q1", "loc1"), ("q2", "loc2"), ("q3", "loc3")])

    def test_unknown_and_inactive_ids_go_to_warnings(self) -> None:
        # E001 已被前置步骤排除 → 活跃集只有 E002、E003
        e1 = _entity("E001", "a")
        e1.exclusions.append(
            Exclusion(tag=EXCLUDED_BY_UNMAINTAINED, rationale="pre", step="pre")
        )
        ctx = _make_ctx([e1, _entity("E002", "b"), _entity("E003", "c")])

        text = """
        {"groups": [
            {"members": ["E002", "E001"], "canonical_name": "ba", "rationale": "r1"},
            {"members": ["E999", "E002"], "canonical_name": "x", "rationale": "r2"}
        ]}
        """
        client = _ClientStub(text=text)
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            record = MergeDuplicatesStep().run(ctx)
        finally:
            _exit_all(patches)

        # 第一组清洗后只剩 [E002] → 单成员不合法，丢弃
        # 第二组清洗后只剩 [E002] → 单成员不合法，丢弃
        self.assertEqual(record.metrics["groups_applied"], 0)
        self.assertEqual(record.metrics["entities_merged"], 0)
        warnings = record.metrics["warnings"]
        self.assertEqual(warnings["inactive_ids"], ["E001"])
        self.assertEqual(warnings["unknown_ids"], ["E999"])
        # E001 的 exclusions 没有被追加 EXCLUDED_BY_DUPLICATE
        self.assertEqual(len(e1.exclusions), 1)
        # ctx.relations 不应被污染
        self.assertEqual(ctx.relations, [])

    def test_skipped_when_fewer_than_two_active(self) -> None:
        # 只有 1 个活跃实体
        e1 = _entity("E001", "a")
        e2 = _entity("E002", "b")
        e2.exclusions.append(
            Exclusion(tag=EXCLUDED_BY_UNMAINTAINED, rationale="pre", step="pre")
        )
        ctx = _make_ctx([e1, e2])

        client = _ClientStub(text='{"groups": []}')
        client.chat = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore
            AssertionError("LLM should not be called when active set < 2")
        )
        patches = _patch_agent_module(client)
        _enter_all(patches)
        try:
            record = MergeDuplicatesStep().run(ctx)
        finally:
            _exit_all(patches)
        self.assertEqual(record.status, StepStatus.SKIPPED)
        self.assertEqual(record.metrics["entities_in"], 1)
        self.assertEqual(record.metrics["entities_out"], 1)


# ---------------------------------------------------------------------------
# Pipeline 装配
# ---------------------------------------------------------------------------


class PipelineWiringTests(unittest.TestCase):
    def test_s1_3_registered_after_s1_2(self) -> None:
        names = list_registered_step_names()
        self.assertIn("s1_3", names)
        self.assertLess(names.index("s1_2"), names.index("s1_3"))

    def test_build_pipeline_until_s1_3(self) -> None:
        pipeline = build_logical_file_pipeline(until="s1_3")
        step_names = [s.name for s in pipeline.steps]
        self.assertEqual(
            step_names,
            [
                "ifpug.s1_1_identify_entities",
                "ifpug.s1_2_filter_unmaintained",
                "ifpug.s1_3_merge_duplicates",
            ],
        )

    def test_build_pipeline_full(self) -> None:
        pipeline = build_logical_file_pipeline()
        step_names = [s.name for s in pipeline.steps]
        self.assertIn("ifpug.s1_3_merge_duplicates", step_names)


if __name__ == "__main__":
    unittest.main()
