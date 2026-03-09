from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from ..models import Document, Project, ProjectSoftwareRelation, Software


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
