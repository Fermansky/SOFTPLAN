import os
from collections.abc import Generator

from sqlalchemy import inspect
from sqlmodel import SQLModel, Session, create_engine

from .models.document_parsing_task import DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY
from .models.layout_analysis_task import DEFAULT_LAYOUT_ANALYSIS_MODEL



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


LEGACY_DOCUMENT_PARSING_INDEXES = (
    "ux_document_parsing_tasks_document_active",
    "ix_document_parsing_tasks_success_by_file_pdf",
    "ix_document_parsing_tasks_success_by_file_pdf_image",
)



def _execute_statements(statements: list[str]) -> None:
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)



def _migrate_legacy_document_parsing_table() -> None:
    if engine.dialect.name != "postgresql":
        return

    with engine.begin() as connection:
        inspector = inspect(connection)
        if not inspector.has_table("document_parsing_tasks") or inspector.has_table("layout_analysis_tasks"):
            return

        connection.exec_driver_sql("ALTER TABLE document_parsing_tasks RENAME TO layout_analysis_tasks")
        for index_name in LEGACY_DOCUMENT_PARSING_INDEXES:
            connection.exec_driver_sql(f"DROP INDEX IF EXISTS {index_name}")



def _ensure_extracted_image_semantic_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    _execute_statements(
        [
            "ALTER TABLE extracted_images ADD COLUMN IF NOT EXISTS semantic_description TEXT",
            "ALTER TABLE extracted_images ADD COLUMN IF NOT EXISTS semantic_description_model TEXT",
            "ALTER TABLE extracted_images ADD COLUMN IF NOT EXISTS semantic_description_updated_at TIMESTAMPTZ",
        ]
    )



def _ensure_extracted_image_semantic_task_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    _execute_statements(
        [
            "ALTER TABLE extracted_image_semantic_tasks ADD COLUMN IF NOT EXISTS overwrite_existing_snapshot BOOLEAN NOT NULL DEFAULT FALSE",
            "DROP INDEX IF EXISTS ux_extracted_image_semantic_tasks_active",
            (
                "CREATE UNIQUE INDEX ux_extracted_image_semantic_tasks_active "
                "ON extracted_image_semantic_tasks (extracted_image_id, target_model_key, overwrite_existing_snapshot) "
                "WHERE status IN ('pending', 'running')"
            ),
        ]
    )



def _ensure_layout_analysis_task_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "ALTER TABLE layout_analysis_tasks ADD COLUMN IF NOT EXISTS requested_layout_model TEXT",
        (
            "ALTER TABLE layout_analysis_tasks "
            f"ADD COLUMN IF NOT EXISTS target_layout_model TEXT NOT NULL DEFAULT '{DEFAULT_LAYOUT_ANALYSIS_MODEL}'"
        ),
        (
            "ALTER TABLE layout_analysis_tasks "
            f"ADD COLUMN IF NOT EXISTS layout_model_key TEXT NOT NULL DEFAULT '{DEFAULT_LAYOUT_ANALYSIS_MODEL}'"
        ),
        "ALTER TABLE layout_analysis_tasks ADD COLUMN IF NOT EXISTS force_layout_analysis BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE layout_analysis_tasks ADD COLUMN IF NOT EXISTS layout_result_source_task_id UUID",
        (
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'layout_analysis_tasks' AND column_name = 'requested_pdf_model') THEN "
            "UPDATE layout_analysis_tasks SET requested_layout_model = requested_pdf_model WHERE requested_layout_model IS NULL; "
            "END IF; "
            "END $$"
        ),
        (
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'layout_analysis_tasks' AND column_name = 'target_pdf_model') THEN "
            f"UPDATE layout_analysis_tasks SET target_layout_model = target_pdf_model WHERE target_layout_model = '{DEFAULT_LAYOUT_ANALYSIS_MODEL}' OR target_layout_model IS NULL; "
            "END IF; "
            "END $$"
        ),
        (
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'layout_analysis_tasks' AND column_name = 'pdf_model_key') THEN "
            f"UPDATE layout_analysis_tasks SET layout_model_key = pdf_model_key WHERE layout_model_key = '{DEFAULT_LAYOUT_ANALYSIS_MODEL}' OR layout_model_key IS NULL; "
            "END IF; "
            "END $$"
        ),
        (
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'layout_analysis_tasks' AND column_name = 'force_pdf_parse') THEN "
            "UPDATE layout_analysis_tasks SET force_layout_analysis = force_pdf_parse WHERE force_layout_analysis = FALSE; "
            "END IF; "
            "END $$"
        ),
        (
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'layout_analysis_tasks' AND column_name = 'pdf_result_source_task_id') THEN "
            "UPDATE layout_analysis_tasks SET layout_result_source_task_id = pdf_result_source_task_id WHERE layout_result_source_task_id IS NULL; "
            "END IF; "
            "END $$"
        ),
        "DROP INDEX IF EXISTS ux_layout_analysis_tasks_document_active",
        "DROP INDEX IF EXISTS ix_layout_analysis_tasks_success_by_file_layout",
        (
            "CREATE UNIQUE INDEX ux_layout_analysis_tasks_document_active "
            "ON layout_analysis_tasks (document_id, layout_model_key) "
            "WHERE status IN ('pending', 'running')"
        ),
        (
            "CREATE INDEX ix_layout_analysis_tasks_success_by_file_layout "
            "ON layout_analysis_tasks (file_id, layout_model_key, created_at DESC) "
            "WHERE status = 'succeeded'"
        ),
    ]
    _execute_statements(statements)



def _ensure_extracted_image_semantic_snapshot_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "DROP INDEX IF EXISTS ux_extracted_image_semantic_snapshots_image_model",
        (
            "CREATE UNIQUE INDEX ux_extracted_image_semantic_snapshots_image_model "
            "ON extracted_image_semantic_snapshots (extracted_image_id, target_model_key)"
        ),
        (
            "INSERT INTO extracted_image_semantic_snapshots "
            "(extracted_image_id, target_model_key, result_model, description, created_at, updated_at) "
            "SELECT id, semantic_description_model, semantic_description_model, semantic_description, "
            "COALESCE(semantic_description_updated_at, created_at), COALESCE(semantic_description_updated_at, created_at) "
            "FROM extracted_images "
            "WHERE semantic_description_model IS NOT NULL AND BTRIM(COALESCE(semantic_description, '')) <> '' "
            "ON CONFLICT (extracted_image_id, target_model_key) DO NOTHING"
        ),
    ]
    _execute_statements(statements)



def _ensure_document_parsing_task_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    statements = [
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS requested_layout_model TEXT",
        (
            "ALTER TABLE document_parsing_tasks "
            f"ADD COLUMN IF NOT EXISTS target_layout_model TEXT NOT NULL DEFAULT '{DEFAULT_LAYOUT_ANALYSIS_MODEL}'"
        ),
        (
            "ALTER TABLE document_parsing_tasks "
            f"ADD COLUMN IF NOT EXISTS layout_model_key TEXT NOT NULL DEFAULT '{DEFAULT_LAYOUT_ANALYSIS_MODEL}'"
        ),
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS requested_image_model TEXT",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS target_image_model TEXT",
        (
            "ALTER TABLE document_parsing_tasks "
            f"ADD COLUMN IF NOT EXISTS image_model_key TEXT NOT NULL DEFAULT '{DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY}'"
        ),
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS force_layout_analysis BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS layout_task_id UUID",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS markdown TEXT",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS image_hashes JSONB NOT NULL DEFAULT '{}'::jsonb",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS image_total_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS image_succeeded_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS image_failed_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ALTER TABLE document_parsing_tasks ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
        "DROP INDEX IF EXISTS ux_document_parsing_tasks_document_active",
        "DROP INDEX IF EXISTS ix_document_parsing_tasks_success_by_file_layout_image",
        (
            "CREATE UNIQUE INDEX ux_document_parsing_tasks_document_active "
            "ON document_parsing_tasks (document_id, layout_model_key, image_model_key) "
            "WHERE status IN ('pending', 'running')"
        ),
        (
            "CREATE INDEX ix_document_parsing_tasks_success_by_file_layout_image "
            "ON document_parsing_tasks (file_id, layout_model_key, image_model_key, created_at DESC) "
            "WHERE status = 'succeeded'"
        ),
    ]
    _execute_statements(statements)



def _ensure_document_parsing_image_item_columns() -> None:
    if engine.dialect.name != "postgresql":
        return

    _execute_statements(
        [
            "DROP INDEX IF EXISTS ux_document_parsing_image_items_task_source_key",
            (
                "CREATE UNIQUE INDEX ux_document_parsing_image_items_task_source_key "
                "ON document_parsing_image_items (document_parsing_task_id, source_key)"
            ),
        ]
    )



def create_db_and_tables() -> None:
    _migrate_legacy_document_parsing_table()
    SQLModel.metadata.create_all(engine)
    _ensure_extracted_image_semantic_columns()
    _ensure_extracted_image_semantic_task_columns()
    _ensure_layout_analysis_task_columns()
    _ensure_extracted_image_semantic_snapshot_columns()
    _ensure_document_parsing_task_columns()
    _ensure_document_parsing_image_item_columns()



def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
