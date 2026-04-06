"""llm-service 应用入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.router import api_router
from .core.logging import configure_logging, install_request_id_middleware
from .services.llm_client import log_openai_compatible_llm_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期钩子。

    启动时输出一次上游 LLM 配置检查日志，便于排查运行时配置问题。
    """
    log_openai_compatible_llm_config()
    yield


def create_app() -> FastAPI:
    """构造 FastAPI 应用并挂载 API 路由。"""
    configure_logging("llm-service")
    app = FastAPI(title="Softplan LLM Service", version="0.1.0", lifespan=lifespan)
    install_request_id_middleware(app)
    app.include_router(api_router)
    return app


app = create_app()
