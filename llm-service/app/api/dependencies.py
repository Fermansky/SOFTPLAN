from ..services import (
    OpenAICompatibleLlmClient,
    get_openai_compatible_llm_client as get_openai_compatible_llm_client_service,
)


def get_openai_compatible_llm_client() -> OpenAICompatibleLlmClient:
    return get_openai_compatible_llm_client_service()
