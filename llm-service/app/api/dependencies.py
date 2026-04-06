"""llm-service API 依赖注入。"""

from ..services import (
    OpenAICompatibleLlmClient,
    get_openai_compatible_llm_client as get_openai_compatible_llm_client_service,
)



def get_openai_compatible_llm_client() -> OpenAICompatibleLlmClient:
    """返回上游 LLM 客户端依赖。"""
    return get_openai_compatible_llm_client_service()
