"""llm-service health router."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from ...core.logging import REQUEST_ID_HEADER, get_request_id
from ...services import BackendProxyClient, BackendProxyError
from ..dependencies import get_backend_proxy_client

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health(
    request: Request,
    client: BackendProxyClient = Depends(get_backend_proxy_client),
):
    request_id = request.headers.get(REQUEST_ID_HEADER) or get_request_id()
    try:
        response = client.get_health(request_id=request_id)
    except BackendProxyError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    proxied_request_id = response.headers.get(REQUEST_ID_HEADER) or request_id
    if proxied_request_id:
        headers[REQUEST_ID_HEADER] = proxied_request_id
    return JSONResponse(status_code=response.status_code, content=response.json(), headers=headers)
