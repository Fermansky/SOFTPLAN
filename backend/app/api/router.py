"""backend 顶层 API 路由聚合。"""

from fastapi import APIRouter

from .routers import (
    document_parsing_router,
    documents_router,
    extracted_images_router,
    health_router,
    llm_router,
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
api_router.include_router(document_parsing_router)
api_router.include_router(llm_router)
api_router.include_router(project_software_relations_router)
