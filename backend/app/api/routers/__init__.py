"""API 路由子模块聚合导出。

职责：
1. 统一暴露各资源路由对象。
2. 供顶层 `api.router` 模块按资源挂载路由。

说明：
- 该模块仅做导出聚合，不扩展任何接口语义。
"""

from .agents import router as agents_router
from .document_parsing import router as document_parsing_router
from .documents import router as documents_router
from .extracted_images import router as extracted_images_router
from .health import router as health_router
from .ifpug import router as ifpug_router
from .layout_analysis import router as layout_analysis_router
from .llm import router as llm_router
from .project_software_relations import router as project_software_relations_router
from .projects import router as projects_router
from .softwares import router as softwares_router

__all__ = [
    "agents_router",
    "document_parsing_router",
    "documents_router",
    "extracted_images_router",
    "health_router",
    "ifpug_router",
    "layout_analysis_router",
    "llm_router",
    "project_software_relations_router",
    "projects_router",
    "softwares_router",
]
