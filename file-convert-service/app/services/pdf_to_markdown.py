import io
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


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

    def _upload_rendered_images(self, images: dict[Any, Any] | None) -> None:
        if not images:
            return

        for image in images.values():
            png_stream = io.BytesIO()
            image.save(png_stream, format="PNG")
            self._image_uploader(png_stream.getvalue(), content_type="image/png")

    def convert(self, payload: bytes) -> str:
        pdf_stream = io.BytesIO(payload)
        rendered = self._pdf_converter(pdf_stream)
        text, _, images = self._text_from_rendered(rendered)
        self._upload_rendered_images(images)
        return text


@lru_cache(maxsize=1)
def get_marker_pdf_to_markdown_converter() -> MarkerPdfToMarkdownConverter:
    logger.info("Initializing Marker PDF-to-Markdown converter")
    return MarkerPdfToMarkdownConverter()
