from fastapi import APIRouter

from .routers import (
    documents_router,
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
api_router.include_router(project_software_relations_router)

