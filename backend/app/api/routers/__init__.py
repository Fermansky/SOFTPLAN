from .document_parsing import router as document_parsing_router
from .documents import router as documents_router
from .extracted_images import router as extracted_images_router
from .health import router as health_router
from .llm import router as llm_router
from .project_software_relations import router as project_software_relations_router
from .projects import router as projects_router
from .softwares import router as softwares_router

__all__ = [
    "document_parsing_router",
    "documents_router",
    "extracted_images_router",
    "health_router",
    "llm_router",
    "project_software_relations_router",
    "projects_router",
    "softwares_router",
]
