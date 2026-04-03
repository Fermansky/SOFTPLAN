import hashlib
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
PdfMarkdownConvertResult = MODULE.PdfMarkdownConvertResult


class _FakeImage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.save_calls = []

    def save(self, output, *, format: str):
        self.save_calls.append(format)
        output.write(self.payload)


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
        converter._image_uploader = lambda *args, **kwargs: None

        result = converter.convert(b"%PDF-1.7\n")

        self.assertIsInstance(result, PdfMarkdownConvertResult)
        self.assertEqual(result.markdown, "# markdown")
        self.assertEqual(result.image_hashes, {})
        self.assertIsInstance(captured["arg"], io.BytesIO)
        self.assertEqual(captured["arg"].getvalue(), b"%PDF-1.7\n")

    def test_convert_uploads_all_rendered_images_as_png(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        image_1 = _FakeImage(b"img-1")
        image_2 = _FakeImage(b"img-2")
        uploader_calls = []

        converter._pdf_converter = lambda file_input: "rendered-object"
        converter._text_from_rendered = lambda rendered: ("# markdown", None, {1: image_1, "b": image_2})

        def fake_image_uploader(payload: bytes, *, content_type: str):
            uploader_calls.append({"payload": payload, "content_type": content_type})

        converter._image_uploader = fake_image_uploader

        result = converter.convert(b"%PDF-1.7\n")

        self.assertEqual(result.markdown, "# markdown")
        self.assertEqual(
            result.image_hashes,
            {
                "1": hashlib.sha256(b"img-1").hexdigest(),
                "b": hashlib.sha256(b"img-2").hexdigest(),
            },
        )
        self.assertEqual(len(uploader_calls), 2)
        self.assertEqual(uploader_calls[0]["payload"], b"img-1")
        self.assertEqual(uploader_calls[1]["payload"], b"img-2")
        self.assertEqual(uploader_calls[0]["content_type"], "image/png")
        self.assertEqual(uploader_calls[1]["content_type"], "image/png")
        self.assertEqual(image_1.save_calls, ["PNG"])
        self.assertEqual(image_2.save_calls, ["PNG"])

    def test_convert_with_empty_images_skips_upload(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        uploader_calls = []
        converter._pdf_converter = lambda file_input: "rendered-object"
        converter._text_from_rendered = lambda rendered: ("# markdown", None, {})
        converter._image_uploader = lambda payload, *, content_type: uploader_calls.append((payload, content_type))

        result = converter.convert(b"%PDF-1.7\n")

        self.assertEqual(result.markdown, "# markdown")
        self.assertEqual(result.image_hashes, {})
        self.assertEqual(uploader_calls, [])

    def test_convert_raises_when_image_upload_fails(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        converter._pdf_converter = lambda file_input: "rendered-object"
        converter._text_from_rendered = lambda rendered: ("# markdown", None, {"a": _FakeImage(b"img")})

        def failing_image_uploader(payload: bytes, *, content_type: str):
            raise RuntimeError("upload failed")

        converter._image_uploader = failing_image_uploader

        with self.assertRaises(RuntimeError) as ctx:
            converter.convert(b"%PDF-1.7\n")

        self.assertIn("upload failed", str(ctx.exception))
