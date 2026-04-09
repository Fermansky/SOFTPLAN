"""健康检查路由。

职责：
1. 提供后端服务基础存活探针。
2. 为网关或部署平台返回最小健康状态。

说明：
- 该路由不检查数据库或外部依赖可用性。
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """返回后端进程级健康状态。"""

    return {"status": "ok"}
