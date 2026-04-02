import io
import importlib.util
import os
from unittest import TestCase

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, "..", "services", "pdf_to_markdown.py"))
SPEC = importlib.util.spec_from_file_location("pdf_to_markdown_module", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Failed to load module spec from {MODULE_PATH}")

MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MarkerPdfToMarkdownConverter = MODULE.MarkerPdfToMarkdownConverter


class MarkerPdfToMarkdownConverterTests(TestCase):
    def test_convert_uses_bytesio_and_returns_text(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        captured = {"arg": None}

        def fake_pdf_converter(file_input):
            captured["arg"] = file_input
            return "rendered-object"

        def fake_text_from_rendered(rendered):
            self.assertEqual(rendered, "rendered-object")
            return "# markdown", None, {}

        converter._pdf_converter = fake_pdf_converter
        converter._text_from_rendered = fake_text_from_rendered

        result = converter.convert(b"%PDF-1.7\n")

        self.assertEqual(result, "# markdown")
        self.assertIsInstance(captured["arg"], io.BytesIO)
        self.assertEqual(captured["arg"].getvalue(), b"%PDF-1.7\n")
