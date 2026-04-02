import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..dependencies import get_file_convert_service_client
from ...services import FileConvertServiceClient

router = APIRouter(prefix="/converters", tags=["converters"])
logger = logging.getLogger(__name__)


class ConverterAvailabilityRead(BaseModel):
    available: bool
    service: str
    health_path: str | None = None
    error: str | None = None


@router.get("/availability", response_model=ConverterAvailabilityRead)
def get_converter_availability(
    client: FileConvertServiceClient = Depends(get_file_convert_service_client),
) -> ConverterAvailabilityRead:
    logger.info("Checking file-convert-service availability")
    available, error = client.check_availability()
    if available:
        return ConverterAvailabilityRead(
            available=True,
            service="file-convert-service",
            health_path="/health",
        )

    logger.warning("file-convert-service is unavailable: %s", error)
    return ConverterAvailabilityRead(
        available=False,
        service="file-convert-service",
        error=error,
    )
