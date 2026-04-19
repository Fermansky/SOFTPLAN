import importlib.util
from pathlib import Path
from unittest import TestCase

_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "llm_json_parser.py"
_SPEC = importlib.util.spec_from_file_location("test_llm_json_parser_module", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load module spec from {_MODULE_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

LlmJsonParseError = _MODULE.LlmJsonParseError
parse = _MODULE.parse
parse_array = _MODULE.parse_array
parse_object = _MODULE.parse_object


class LlmJsonParserTests(TestCase):
    def test_parse_accepts_plain_json_object(self):
        result = parse('{"name":"demo","count":2}')

        self.assertEqual(result, {"name": "demo", "count": 2})

    def test_parse_accepts_plain_json_array(self):
        result = parse('[{"id":1},{"id":2}]')

        self.assertEqual(result, [{"id": 1}, {"id": 2}])

    def test_parse_extracts_json_from_json_code_fence(self):
        result = parse(
            """Here is the payload:

```json
{"status":"ok","items":[1,2,3]}
```
"""
        )

        self.assertEqual(result, {"status": "ok", "items": [1, 2, 3]})

    def test_parse_extracts_json_from_plain_code_fence(self):
        result = parse(
            """Use this:

```
{"status":"ok","source":"code-block"}
```
"""
        )

        self.assertEqual(result, {"status": "ok", "source": "code-block"})

    def test_parse_extracts_first_balanced_json_from_text(self):
        result = parse('Model reply: success payload={"answer":42,"ok":true} thanks.')

        self.assertEqual(result, {"answer": 42, "ok": True})

    def test_parse_handles_nested_json_structures(self):
        result = parse(
            'prefix {"items":[{"meta":{"tags":["a","b"]}},{"meta":{"tags":[]}}],"ok":true} suffix'
        )

        self.assertEqual(
            result,
            {
                "items": [
                    {"meta": {"tags": ["a", "b"]}},
                    {"meta": {"tags": []}},
                ],
                "ok": True,
            },
        )

    def test_parse_does_not_break_on_brackets_inside_json_strings(self):
        result = parse(
            'prefix {"message":"keep {these} [chars] and \\"quotes\\"","ok":true} suffix'
        )

        self.assertEqual(result, {"message": 'keep {these} [chars] and "quotes"', "ok": True})

    def test_parse_raises_for_empty_text(self):
        with self.assertRaises(LlmJsonParseError):
            parse("   ")

    def test_parse_raises_when_no_json_exists(self):
        with self.assertRaises(LlmJsonParseError):
            parse("No structured payload is available here.")

    def test_parse_raises_when_only_incomplete_json_exists(self):
        with self.assertRaises(LlmJsonParseError):
            parse('prefix {"missing": true suffix')

    def test_parse_object_raises_for_top_level_array(self):
        with self.assertRaises(LlmJsonParseError) as ctx:
            parse_object('[{"id":1}]')

        self.assertIn("Expected top-level JSON object", str(ctx.exception))

    def test_parse_array_raises_for_top_level_object(self):
        with self.assertRaises(LlmJsonParseError) as ctx:
            parse_array('{"id":1}')

        self.assertIn("Expected top-level JSON array", str(ctx.exception))

    def test_parse_object_returns_object(self):
        result = parse_object('note: {"kind":"object","valid":true}')

        self.assertEqual(result, {"kind": "object", "valid": True})

    def test_parse_array_returns_array(self):
        result = parse_array('note: [1,2,3]')

        self.assertEqual(result, [1, 2, 3])
