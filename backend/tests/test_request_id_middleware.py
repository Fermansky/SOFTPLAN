from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main as backend_main


class BackendRequestIdMiddlewareTests(TestCase):
    def test_backend_health_reuses_request_id_header(self):
        with patch.object(backend_main, "create_db_and_tables", lambda: None):
            with patch.object(backend_main, "_log_extracted_image_semantic_prompt_status", lambda: None):
                with patch.object(backend_main, "is_layout_analysis_task_worker_enabled", return_value=False):
                    with patch.object(backend_main, "is_extracted_image_semantic_task_worker_enabled", return_value=False):
                        with TestClient(backend_main.create_app()) as client:
                            response = client.get("/health", headers={"X-Request-ID": "req-backend-1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "req-backend-1")

    def test_backend_health_generates_request_id_header(self):
        with patch.object(backend_main, "create_db_and_tables", lambda: None):
            with patch.object(backend_main, "_log_extracted_image_semantic_prompt_status", lambda: None):
                with patch.object(backend_main, "is_layout_analysis_task_worker_enabled", return_value=False):
                    with patch.object(backend_main, "is_extracted_image_semantic_task_worker_enabled", return_value=False):
                        with TestClient(backend_main.create_app()) as client:
                            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)
        self.assertTrue(response.headers["X-Request-ID"])
