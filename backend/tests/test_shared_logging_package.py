from unittest import TestCase

from backend.app.core import logging as backend_logging
from softplan_common import logging as shared_logging


class SharedLoggingPackageTests(TestCase):
    def test_backend_wrapper_reuses_shared_logging_exports(self):
        self.assertIs(backend_logging.configure_logging, shared_logging.configure_logging)
        self.assertIs(backend_logging.install_request_id_middleware, shared_logging.install_request_id_middleware)
        self.assertIs(backend_logging.build_log_extra, shared_logging.build_log_extra)
        self.assertEqual(backend_logging.REQUEST_ID_HEADER, shared_logging.REQUEST_ID_HEADER)
