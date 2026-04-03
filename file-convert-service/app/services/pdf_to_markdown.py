import hashlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
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
class UploadedImageMetadata:
    source_key: str
    file_hash: str
    storage_bucket: str
    storage_key: str
    file_size: int
    content_type: str
    extension: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True)
class PdfMarkdownConvertResult:
    markdown: str
    image_hashes: dict[str, str]
    uploaded_images: list[UploadedImageMetadata] = field(default_factory=list)


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

    def _coerce_optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _upload_rendered_images(self, images: dict[Any, Any] | None) -> tuple[dict[str, str], list[UploadedImageMetadata]]:
        if not images:
            return {}, []

        image_hashes: dict[str, str] = {}
        uploaded_images: list[UploadedImageMetadata] = []
        for image_key, image in images.items():
            normalized_key = str(image_key)
            payload, content_type = self._serialize_with_fallback(image, image_key=normalized_key)
            storage_ref = self._image_uploader(payload, content_type=content_type)
            payload_hash = hashlib.sha256(payload).hexdigest()
            extension = Path(storage_ref.storage_key).suffix.lower() or None

            image_hashes[normalized_key] = payload_hash
            uploaded_images.append(
                UploadedImageMetadata(
                    source_key=normalized_key,
                    file_hash=payload_hash,
                    storage_bucket=storage_ref.bucket,
                    storage_key=storage_ref.storage_key,
                    file_size=len(payload),
                    content_type=content_type,
                    extension=extension,
                    width=self._coerce_optional_int(getattr(image, "width", None)),
                    height=self._coerce_optional_int(getattr(image, "height", None)),
                )
            )
        return image_hashes, uploaded_images

    def convert(self, payload: bytes) -> PdfMarkdownConvertResult:
        pdf_stream = io.BytesIO(payload)
        rendered = self._pdf_converter(pdf_stream)
        text, _, images = self._text_from_rendered(rendered)
        image_hashes, uploaded_images = self._upload_rendered_images(images)
        return PdfMarkdownConvertResult(
            markdown=text,
            image_hashes=image_hashes,
            uploaded_images=uploaded_images,
        )


@lru_cache(maxsize=1)
def get_marker_pdf_to_markdown_converter() -> MarkerPdfToMarkdownConverter:
    logger.info("Initializing Marker PDF-to-Markdown converter")
    return MarkerPdfToMarkdownConverter()
