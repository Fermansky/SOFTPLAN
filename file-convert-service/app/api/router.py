from fastapi import APIRouter

from .routers import health_router, storage_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(storage_router)
