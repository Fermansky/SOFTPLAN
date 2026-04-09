"""API 包级导出。

职责：
1. 暴露 backend API 的统一路由入口。
2. 为应用初始化提供稳定导入路径。

说明：
- 该模块仅做导出聚合，不承载路由逻辑。
"""

from .router import api_router

__all__ = ["api_router"]
