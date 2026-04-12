import logging
import os
from unittest import TestCase
from unittest.mock import patch

from backend.app.core import logging as logging_module


class LoggingConfigTests(TestCase):
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
            logging_module.configure_logging("backend-test")

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
            logging_module.configure_logging("backend-test")

        root_logger = logging.getLogger()
        uvicorn_access_logger = logging.getLogger("uvicorn.access")

        self.assertEqual(root_logger.level, logging.INFO)
        self.assertEqual(root_logger.handlers[0].formatter.__class__.__name__, "ConsoleFormatter")
        self.assertTrue(uvicorn_access_logger.handlers)

    def test_build_log_extra_skips_none_fields(self):
        extra = logging_module.build_log_extra(
            "logging.configured",
            environment="development",
            request_id=None,
        )

        self.assertEqual(
            extra,
            {
                "event": "logging.configured",
                "environment": "development",
            },
        )
