"""llm-service API dependency injection."""

from ..services import BackendProxyClient, get_backend_proxy_client as get_backend_proxy_client_service


def get_backend_proxy_client() -> BackendProxyClient:
    return get_backend_proxy_client_service()
