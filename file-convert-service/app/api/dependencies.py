from ..services import (
    MarkerPdfToMarkdownConverter,
    MinioStorage,
    get_marker_pdf_to_markdown_converter as get_marker_pdf_to_markdown_converter_service,
    get_minio_storage as get_minio_storage_service,
)


def get_minio_storage() -> MinioStorage:
    return get_minio_storage_service()


def get_marker_pdf_to_markdown_converter() -> MarkerPdfToMarkdownConverter:
    return get_marker_pdf_to_markdown_converter_service()
