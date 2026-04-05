from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from ..models import Document, ExtractedImage, FileRecord, Project, ProjectSoftwareRelation, Software
from ..services import (
    FileConvertServiceClient,
    LlmServiceClient,
    MinioStorage,
    get_file_convert_service_client as get_file_convert_service_client_service,
    get_llm_service_client as get_llm_service_client_service,
    get_minio_storage as get_minio_storage_service,
)


def get_active_project_or_404(project_id: UUID, session: Session) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    project = session.exec(statement).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_software_or_404(software_id: UUID, session: Session) -> Software:
    statement = select(Software).where(Software.id == software_id, Software.deleted_at.is_(None))
    software = session.exec(statement).first()
    if software is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Software not found")
    return software


def get_active_document_or_404(document_id: UUID, session: Session) -> Document:
    statement = select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    document = session.exec(statement).first()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def get_file_or_404(file_id: UUID, session: Session) -> FileRecord:
    statement = select(FileRecord).where(FileRecord.id == file_id)
    file_record = session.exec(statement).first()
    if file_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file_record


def get_extracted_image_or_404(image_id: int, session: Session) -> ExtractedImage:
    statement = select(ExtractedImage).where(ExtractedImage.id == image_id)
    extracted_image = session.exec(statement).first()
    if extracted_image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extracted image not found")
    return extracted_image


def get_project_software_relation_or_404(
    project_id: UUID, software_id: UUID, session: Session
) -> ProjectSoftwareRelation:
    statement = select(ProjectSoftwareRelation).where(
        ProjectSoftwareRelation.project_id == project_id,
        ProjectSoftwareRelation.software_id == software_id,
    )
    relation = session.exec(statement).first()
    if relation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project software relation not found",
        )
    return relation


def get_minio_storage() -> MinioStorage:
    return get_minio_storage_service()


def get_file_convert_service_client() -> FileConvertServiceClient:
    return get_file_convert_service_client_service()


def get_llm_service_client() -> LlmServiceClient:
    return get_llm_service_client_service()
