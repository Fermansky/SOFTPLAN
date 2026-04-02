import logging
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)


class MarkerPdfToMarkdownConverter:
    def __init__(self) -> None:
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
        except ImportError as exc:
            raise RuntimeError(
                "Marker dependencies are not installed. Install marker-pdf in file-convert-service."
            ) from exc

        self._pdf_converter = PdfConverter(artifact_dict=create_model_dict())
        self._text_from_rendered = text_from_rendered

    def convert(self, payload: bytes) -> str:
        with TemporaryDirectory(prefix="softplan-marker-") as temp_dir:
            pdf_path = Path(temp_dir) / "input.pdf"
            pdf_path.write_bytes(payload)
            rendered = self._pdf_converter(str(pdf_path))
            text, _, _ = self._text_from_rendered(rendered)
            return text


@lru_cache(maxsize=1)
def get_marker_pdf_to_markdown_converter() -> MarkerPdfToMarkdownConverter:
    logger.info("Initializing Marker PDF-to-Markdown converter")
    return MarkerPdfToMarkdownConverter()
