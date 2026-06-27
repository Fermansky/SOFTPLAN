"""IFPUG 流水线整体装配测试（与单个 step 解耦）。

只验证：
- 已注册步骤的短名顺序符合预期（s1_1 → s1_2 → s1_3 → s1_4 → s1_5 → s1_6）
- ``until`` 截断在每个有效短名上都返回对应前缀
- ``until`` 给未知短名抛 ValueError
- 全量构造产生的实际 step 类型与预期一致
"""

from __future__ import annotations

import unittest

from backend.app.agents.ifpug import (
    FilterAssociativeStep,
    FilterCodeDataStep,
    FilterNotUserRequiredStep,
    FilterUnmaintainedStep,
    IdentifyEntitiesStep,
    MergeDuplicatesStep,
    build_logical_file_pipeline,
    list_registered_step_names,
)


_EXPECTED_SHORT_NAMES = ["s1_1", "s1_2", "s1_3", "s1_4", "s1_5", "s1_6"]
_EXPECTED_STEP_NAMES = [
    "ifpug.s1_1_identify_entities",
    "ifpug.s1_2_filter_unmaintained",
    "ifpug.s1_3_merge_duplicates",
    "ifpug.s1_4_filter_code_data",
    "ifpug.s1_5_filter_not_user_required",
    "ifpug.s1_6_filter_associative",
]
_EXPECTED_STEP_TYPES = [
    IdentifyEntitiesStep,
    FilterUnmaintainedStep,
    MergeDuplicatesStep,
    FilterCodeDataStep,
    FilterNotUserRequiredStep,
    FilterAssociativeStep,
]


class IfpugPipelineAssemblyTests(unittest.TestCase):
    def test_registered_short_names_are_in_expected_order(self) -> None:
        self.assertEqual(list_registered_step_names(), _EXPECTED_SHORT_NAMES)

    def test_full_pipeline_contains_all_steps_in_order(self) -> None:
        pipeline = build_logical_file_pipeline()
        actual_names = [s.name for s in pipeline.steps]
        self.assertEqual(actual_names, _EXPECTED_STEP_NAMES)
        for step, expected_type in zip(pipeline.steps, _EXPECTED_STEP_TYPES):
            self.assertIsInstance(step, expected_type)

    def test_until_truncates_correctly_at_each_short_name(self) -> None:
        for index, short_name in enumerate(_EXPECTED_SHORT_NAMES):
            with self.subTest(until=short_name):
                pipeline = build_logical_file_pipeline(until=short_name)
                self.assertEqual(
                    [s.name for s in pipeline.steps],
                    _EXPECTED_STEP_NAMES[: index + 1],
                )

    def test_until_unknown_short_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_logical_file_pipeline(until="s9_9")


if __name__ == "__main__":
    unittest.main()
