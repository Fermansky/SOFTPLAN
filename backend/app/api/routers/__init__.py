from .converters import router as converters_router
from .documents import router as documents_router
from .health import router as health_router
from .project_software_relations import router as project_software_relations_router
from .projects import router as projects_router
from .softwares import router as softwares_router

__all__ = [
    "converters_router",
    "documents_router",
    "health_router",
    "project_software_relations_router",
    "projects_router",
    "softwares_router",
]
