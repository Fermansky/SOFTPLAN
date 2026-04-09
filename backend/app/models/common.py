"""模型层公共辅助。

职责：
1. 提供 models 共享的轻量工具函数。
2. 统一生成带时区的 UTC 时间戳默认值。

说明：
- 不承载具体业务语义。
- 不负责时区转换或持久化策略。
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前 UTC 时间。"""

    return datetime.now(timezone.utc)
