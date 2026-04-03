import hashlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_IMAGE_ENCODING_BY_EXTENSION: dict[str, tuple[str, str]] = {
    "jpg": ("JPEG", "image/jpeg"),
    "jpeg": ("JPEG", "image/jpeg"),
    "png": ("PNG", "image/png"),
    "webp": ("WEBP", "image/webp"),
    "gif": ("GIF", "image/gif"),
    "bmp": ("BMP", "image/bmp"),
    "tif": ("TIFF", "image/tiff"),
    "tiff": ("TIFF", "image/tiff"),
}
_DEFAULT_IMAGE_ENCODING = ("PNG", "image/png")


@dataclass(frozen=True)
class PdfMarkdownConvertResult:
    markdown: str
    image_hashes: dict[str, str]


class MarkerPdfToMarkdownConverter:
    def __init__(self, image_uploader: Callable[..., Any] | None = None) -> None:
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
        except ImportError as exc:
            raise RuntimeError(
                "Marker dependencies are not installed. Install marker-pdf in file-convert-service."
            ) from exc

        if image_uploader is None:
            from .image_upload_service import upload_image_bytes

            self._image_uploader = upload_image_bytes
        else:
            self._image_uploader = image_uploader

        self._pdf_converter = PdfConverter(artifact_dict=create_model_dict())
        self._text_from_rendered = text_from_rendered

    def _resolve_image_encoding(self, image_key: str) -> tuple[str, str]:
        extension = image_key.rsplit(".", 1)[-1].strip().lower() if "." in image_key else ""
        return _IMAGE_ENCODING_BY_EXTENSION.get(extension, _DEFAULT_IMAGE_ENCODING)

    def _serialize_image(self, image: Any, *, image_format: str) -> bytes:
        output = io.BytesIO()
        image.save(output, format=image_format)
        return output.getvalue()

    def _serialize_with_fallback(self, image: Any, *, image_key: str) -> tuple[bytes, str]:
        image_format, content_type = self._resolve_image_encoding(image_key)
        try:
            payload = self._serialize_image(image, image_format=image_format)
            return payload, content_type
        except Exception:
            if image_format == "PNG":
                raise
            logger.warning(
                "Failed to serialize rendered image with inferred format, fallback to PNG, image_key=%s, format=%s",
                image_key,
                image_format,
            )
            payload = self._serialize_image(image, image_format="PNG")
            return payload, "image/png"

    def _upload_rendered_images(self, images: dict[Any, Any] | None) -> dict[str, str]:
        if not images:
            return {}

        image_hashes: dict[str, str] = {}
        for image_key, image in images.items():
            normalized_key = str(image_key)
            payload, content_type = self._serialize_with_fallback(image, image_key=normalized_key)
            self._image_uploader(payload, content_type=content_type)
            image_hashes[normalized_key] = hashlib.sha256(payload).hexdigest()
        return image_hashes

    def convert(self, payload: bytes) -> PdfMarkdownConvertResult:
        pdf_stream = io.BytesIO(payload)
        rendered = self._pdf_converter(pdf_stream)
        text, _, images = self._text_from_rendered(rendered)
        image_hashes = self._upload_rendered_images(images)
        return PdfMarkdownConvertResult(markdown=text, image_hashes=image_hashes)


@lru_cache(maxsize=1)
def get_marker_pdf_to_markdown_converter() -> MarkerPdfToMarkdownConverter:
    logger.info("Initializing Marker PDF-to-Markdown converter")
    return MarkerPdfToMarkdownConverter()
