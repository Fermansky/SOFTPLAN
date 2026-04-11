import logging
import os
import sys
from unittest import TestCase
from unittest.mock import patch

CURRENT_DIR = os.path.dirname(__file__)
SERVICE_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "..", ".."))
if SERVICE_ROOT not in sys.path:
    sys.path.insert(0, SERVICE_ROOT)

from app.core import logging as logging_module  # noqa: E402


class LoggingConfigTests(TestCase):
    def tearDown(self) -> None:
        logging.shutdown()

    def test_configure_logging_uses_json_formatter_for_production_auto(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "APP_LOG_LEVEL": "WARNING",
                "APP_LOG_FORMAT": "auto",
                "APP_LOG_ACCESS_ENABLED": "false",
            },
            clear=False,
        ):
            logging_module.configure_logging("file-convert-service-test")

        root_logger = logging.getLogger()
        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        uvicorn_access_logger = logging.getLogger("uvicorn.access")

        self.assertEqual(root_logger.level, logging.WARNING)
        self.assertTrue(root_logger.handlers)
        self.assertEqual(root_logger.handlers[0].formatter.__class__.__name__, "JsonLogFormatter")
        self.assertTrue(uvicorn_error_logger.handlers)
        self.assertEqual(uvicorn_access_logger.handlers, [])

    def test_configure_logging_uses_console_formatter_for_development_auto(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "APP_LOG_LEVEL": "INFO",
                "APP_LOG_FORMAT": "auto",
                "APP_LOG_ACCESS_ENABLED": "true",
            },
            clear=False,
        ):
            logging_module.configure_logging("file-convert-service-test")

        root_logger = logging.getLogger()
        uvicorn_access_logger = logging.getLogger("uvicorn.access")

        self.assertEqual(root_logger.level, logging.INFO)
        self.assertEqual(root_logger.handlers[0].formatter.__class__.__name__, "ConsoleFormatter")
        self.assertTrue(uvicorn_access_logger.handlers)


class LoggingHelpersTests(TestCase):
    def test_build_log_extra_omits_none_values(self):
        extra = logging_module.build_log_extra("demo.event", request_id="req-1", empty=None, count=2)

        self.assertEqual(extra, {"event": "demo.event", "request_id": "req-1", "count": 2})

    def test_build_log_extra_renames_reserved_log_record_fields(self):
        extra = logging_module.build_log_extra("demo.event", filename="demo.pdf", module="converters")

        self.assertEqual(extra["file_name"], "demo.pdf")
        self.assertEqual(extra["module_name"], "converters")
        self.assertNotIn("filename", extra)
        self.assertNotIn("module", extra)

    def test_get_request_id_returns_none_for_missing_value(self):
        token = logging_module._set_request_id(None)
        try:
            self.assertIsNone(logging_module.get_request_id())
        finally:
            logging_module._reset_request_id(token)

    def test_get_request_id_returns_none_for_blank_value(self):
        token = logging_module._set_request_id("   ")
        try:
            self.assertIsNone(logging_module.get_request_id())
        finally:
            logging_module._reset_request_id(token)
