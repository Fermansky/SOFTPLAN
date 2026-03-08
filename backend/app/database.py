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


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
