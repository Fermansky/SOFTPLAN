import os
from collections.abc import Generator

from sqlmodel import SQLModel, Session, create_engine


def _build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    user = os.getenv("POSTGRES_USER", "softplan")
    password = os.getenv("POSTGRES_PASSWORD", "softplan")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "softplan")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


DATABASE_URL = _build_database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def _ensure_extracted_image_semantic_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "ALTER TABLE extracted_images ADD COLUMN IF NOT EXISTS semantic_description TEXT",
        "ALTER TABLE extracted_images ADD COLUMN IF NOT EXISTS semantic_description_model TEXT",
        "ALTER TABLE extracted_images ADD COLUMN IF NOT EXISTS semantic_description_updated_at TIMESTAMPTZ",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def _ensure_extracted_image_semantic_task_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "ALTER TABLE extracted_image_semantic_tasks ADD COLUMN IF NOT EXISTS overwrite_existing_snapshot BOOLEAN NOT NULL DEFAULT FALSE",
        "DROP INDEX IF EXISTS ux_extracted_image_semantic_tasks_active",
        (
            "CREATE UNIQUE INDEX ux_extracted_image_semantic_tasks_active "
            "ON extracted_image_semantic_tasks (extracted_image_id, target_model_key, overwrite_existing_snapshot) "
            "WHERE status IN ('pending', 'running')"
        ),
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_extracted_image_semantic_columns()
    _ensure_extracted_image_semantic_task_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session