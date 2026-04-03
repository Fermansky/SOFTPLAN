from fastapi import APIRouter

from .routers import (
    converters_router,
    documents_router,
    extracted_images_router,
    health_router,
    project_software_relations_router,
    projects_router,
    softwares_router,
)

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(softwares_router)
api_router.include_router(documents_router)
api_router.include_router(extracted_images_router)
api_router.include_router(converters_router)
api_router.include_router(project_software_relations_router)
