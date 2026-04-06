"""llm-service 顶层 API 路由聚合。"""

from fastapi import APIRouter

from .routers import health_router, llm_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(llm_router)
