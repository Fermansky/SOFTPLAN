import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .database import create_db_and_tables, get_session
from .models import Project, ProjectCreate, ProjectRead, ProjectUpdate

app = FastAPI(title="Softplan API", version="0.1.0")

allowed_origins = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    create_db_and_tables()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _get_active_project_or_404(project_id: UUID, session: Session) -> Project:
    statement = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    project = session.exec(statement).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@app.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)) -> Project:
    project = Project(**payload.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@app.get("/projects", response_model=list[ProjectRead])
def list_projects(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Project]:
    statement = (
        select(Project)
        .where(Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


@app.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID, session: Session = Depends(get_session)) -> Project:
    return _get_active_project_or_404(project_id, session)


@app.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: Session = Depends(get_session),
) -> Project:
    project = _get_active_project_or_404(project_id, session)

    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(project, field_name, value)
    project.updated_at = datetime.now(timezone.utc)

    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@app.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: UUID, session: Session = Depends(get_session)) -> Response:
    project = _get_active_project_or_404(project_id, session)
    now = datetime.now(timezone.utc)
    project.deleted_at = now
    project.updated_at = now
    session.add(project)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
