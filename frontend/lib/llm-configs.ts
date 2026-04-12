export type LlmConfigProvider = "openai_compatible"
export type LlmValidationDepth = "basic" | "strict"

export type ApiLlmConfig = {
  id: string
  code: string
  name: string
  provider: LlmConfigProvider
  base_url: string
  default_model: string
  timeout_seconds: number
  is_active: boolean
  enabled: boolean
  has_api_key: boolean
  api_key_masked: string | null
  created_at: string
  updated_at: string
}

export type ApiLlmConfigListItem = ApiLlmConfig

export type CreateLlmConfigPayload = {
  code: string
  name: string
  provider: LlmConfigProvider
  base_url: string
  api_key: string
  default_model: string
  timeout_seconds: number
  enabled: boolean
  is_active: boolean
}

export type UpdateLlmConfigPayload = {
  name?: string
  provider?: LlmConfigProvider
  base_url?: string
  api_key?: string
  default_model?: string
  timeout_seconds?: number
  enabled?: boolean
}

export type LlmConfigValidationResult = {
  valid: boolean
  stage: string
  normalized_base_url: string
  model_checked: boolean
  latency_ms: number | null
  http_status: number | null
  error_code: string | null
  error_message: string | null
}

export type LlmConfigModelsResult = {
  success: boolean
  normalized_base_url: string
  model_ids: string[]
  latency_ms: number | null
  http_status: number | null
  error_code: string | null
  error_message: string | null
}

export type PreviewLlmConfigModelsPayload = {
  provider: LlmConfigProvider
  base_url: string
  api_key: string
  timeout_seconds: number
}

export function getLlmApiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
}

async function parseErrorMessage(response: Response, fallbackMessage: string) {
  try {
    const errorData = (await response.json()) as { detail?: string }
    if (errorData.detail) {
      return errorData.detail
    }
  } catch {
    // Keep the fallback message when the response body is not JSON.
  }

  return fallbackMessage
}

export async function listLlmConfigs(signal?: AbortSignal): Promise<ApiLlmConfigListItem[]> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载模型配置列表失败（HTTP ${response.status}）`))
  }

  const data = (await response.json()) as ApiLlmConfigListItem[]
  return Array.isArray(data) ? data : []
}

export async function fetchLlmConfig(configId: string, signal?: AbortSignal): Promise<ApiLlmConfig> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs/${configId}`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载模型配置详情失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiLlmConfig
}

export async function createLlmConfig(payload: CreateLlmConfigPayload): Promise<ApiLlmConfig> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `创建模型配置失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiLlmConfig
}

export async function updateLlmConfig(configId: string, payload: UpdateLlmConfigPayload): Promise<ApiLlmConfig> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs/${configId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `保存模型配置失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiLlmConfig
}

export async function activateLlmConfig(configId: string): Promise<ApiLlmConfig> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs/${configId}/activate`, {
    method: "POST",
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `激活模型配置失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiLlmConfig
}

export async function validateLlmConfig(
  configId: string,
  depth: LlmValidationDepth = "strict"
): Promise<LlmConfigValidationResult> {
  const query = new URLSearchParams({ depth })
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs/${configId}/validate?${query.toString()}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `校验模型配置失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as LlmConfigValidationResult
}

export async function fetchLlmConfigModels(
  configId: string,
  signal?: AbortSignal
): Promise<LlmConfigModelsResult> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs/${configId}/models`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载模型列表失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as LlmConfigModelsResult
}

export async function previewLlmConfigModels(
  payload: PreviewLlmConfigModelsPayload,
  signal?: AbortSignal
): Promise<LlmConfigModelsResult> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/models/preview`, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `预探测模型列表失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as LlmConfigModelsResult
}

export async function deleteLlmConfig(configId: string): Promise<void> {
  const response = await fetch(`${getLlmApiBaseUrl()}/llm/configs/${configId}`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `删除模型配置失败（HTTP ${response.status}）`))
  }
}
