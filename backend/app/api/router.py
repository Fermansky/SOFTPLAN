"""backend 顶层 API 路由聚合。

职责：
1. 汇总各资源路由并挂载到统一 API Router。
2. 维持路由注册顺序与公开入口的一致性。

说明：
- 该模块只负责聚合，不处理请求编排或依赖注入。
"""

from fastapi import APIRouter

from .routers import (
    agents_router,
    document_parsing_router,
    documents_router,
    extracted_images_router,
    health_router,
    layout_analysis_router,
    llm_router,
    project_software_relations_router,
    projects_router,
    softwares_router,
)

api_router = APIRouter()
api_router.include_router(agents_router)
api_router.include_router(health_router)
api_router.include_router(projects_router)
api_router.include_router(softwares_router)
api_router.include_router(documents_router)
api_router.include_router(extracted_images_router)
api_router.include_router(layout_analysis_router)
api_router.include_router(document_parsing_router)
api_router.include_router(llm_router)
api_router.include_router(project_software_relations_router)
