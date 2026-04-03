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
    def __init__(
        self,
        payload: bytes,
        *,
        payload_by_format: dict[str, bytes] | None = None,
        fail_formats: set[str] | None = None,
    ):
        self.payload = payload
        self.payload_by_format = payload_by_format or {}
        self.fail_formats = fail_formats or set()
        self.save_calls = []

    def save(self, output, *, format: str):
        self.save_calls.append(format)
        if format in self.fail_formats:
            raise OSError(f"cannot save as {format}")
        output.write(self.payload_by_format.get(format, self.payload))


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

    def test_convert_uploads_images_using_format_inferred_from_key(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        jpeg_payload = b"jpeg-encoded"
        png_payload = b"png-encoded"
        image_jpeg = _FakeImage(b"fallback", payload_by_format={"JPEG": jpeg_payload})
        image_png = _FakeImage(b"fallback", payload_by_format={"PNG": png_payload})
        uploader_calls = []

        converter._pdf_converter = lambda file_input: "rendered-object"
        converter._text_from_rendered = lambda rendered: (
            "# markdown",
            None,
            {
                "_page_3_Picture_2.jpeg": image_jpeg,
                "diagram.png": image_png,
            },
        )

        def fake_image_uploader(payload: bytes, *, content_type: str):
            uploader_calls.append({"payload": payload, "content_type": content_type})

        converter._image_uploader = fake_image_uploader

        result = converter.convert(b"%PDF-1.7\n")

        self.assertEqual(result.markdown, "# markdown")
        self.assertEqual(
            result.image_hashes,
            {
                "_page_3_Picture_2.jpeg": hashlib.sha256(jpeg_payload).hexdigest(),
                "diagram.png": hashlib.sha256(png_payload).hexdigest(),
            },
        )
        self.assertEqual(len(uploader_calls), 2)
        self.assertEqual(uploader_calls[0]["payload"], jpeg_payload)
        self.assertEqual(uploader_calls[0]["content_type"], "image/jpeg")
        self.assertEqual(uploader_calls[1]["payload"], png_payload)
        self.assertEqual(uploader_calls[1]["content_type"], "image/png")
        self.assertEqual(image_jpeg.save_calls, ["JPEG"])
        self.assertEqual(image_png.save_calls, ["PNG"])

    def test_convert_with_unknown_extension_defaults_to_png(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        png_payload = b"unknown-ext-png"
        image = _FakeImage(b"fallback", payload_by_format={"PNG": png_payload})
        uploader_calls = []

        converter._pdf_converter = lambda file_input: "rendered-object"
        converter._text_from_rendered = lambda rendered: ("# markdown", None, {"marker_image_unknown": image})
        converter._image_uploader = lambda payload, *, content_type: uploader_calls.append(
            {"payload": payload, "content_type": content_type}
        )

        result = converter.convert(b"%PDF-1.7\n")

        self.assertEqual(result.image_hashes, {"marker_image_unknown": hashlib.sha256(png_payload).hexdigest()})
        self.assertEqual(uploader_calls, [{"payload": png_payload, "content_type": "image/png"}])
        self.assertEqual(image.save_calls, ["PNG"])

    def test_convert_falls_back_to_png_when_inferred_format_save_fails(self):
        converter = MarkerPdfToMarkdownConverter.__new__(MarkerPdfToMarkdownConverter)

        png_payload = b"fallback-png"
        image = _FakeImage(
            b"unused",
            payload_by_format={"PNG": png_payload},
            fail_formats={"JPEG"},
        )
        uploader_calls = []

        converter._pdf_converter = lambda file_input: "rendered-object"
        converter._text_from_rendered = lambda rendered: ("# markdown", None, {"photo.jpg": image})
        converter._image_uploader = lambda payload, *, content_type: uploader_calls.append(
            {"payload": payload, "content_type": content_type}
        )

        result = converter.convert(b"%PDF-1.7\n")

        self.assertEqual(result.image_hashes, {"photo.jpg": hashlib.sha256(png_payload).hexdigest()})
        self.assertEqual(uploader_calls, [{"payload": png_payload, "content_type": "image/png"}])
        self.assertEqual(image.save_calls, ["JPEG", "PNG"])

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
