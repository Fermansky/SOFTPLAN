from unittest import TestCase
from unittest.mock import patch

from sqlmodel import SQLModel

from backend.app import database as database_module


class DatabaseInitializationTests(TestCase):
    def test_create_db_and_tables_only_creates_current_metadata(self):
        with patch.object(SQLModel.metadata, "create_all") as create_all_mock:
            database_module.create_db_and_tables()

        create_all_mock.assert_called_once_with(database_module.engine)

    def test_current_metadata_contains_core_tables(self):
        table_names = set(SQLModel.metadata.tables)

        self.assertTrue(
            {
                "llm_configs",
                "llm_chat_records",
                "layout_analysis_tasks",
                "document_parsing_tasks",
            }.issubset(table_names)
        )
