export type ApiDocument = {
  id: string
  file_id: string | null
  project_id: string
  software_id: string | null
  name: string
  description: string
  extra_info: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export type ApiDocumentParsingImageSemantic = {
  description: string
  result_model: string | null
  source_task_id: string | null
  updated_at: string
}

export type ApiDocumentParsingImageItem = {
  id: number
  source_key: string
  file_hash: string
  extracted_image_id: number
  semantic_task_id: string | null
  status: "pending" | "running" | "succeeded" | "failed"
  result_source: "submitted_semantic_task" | "reused_semantic_task" | "semantic_snapshot" | null
  error_message: string | null
  semantic: ApiDocumentParsingImageSemantic | null
  created_at: string
  updated_at: string
}

export type ApiDocumentParsingTask = {
  id: string
  document_id: string
  file_id: string
  storage_bucket: string
  storage_key: string
  requested_layout_model: string | null
  target_layout_model: string
  layout_model_key: string
  requested_image_model: string | null
  target_image_model: string | null
  image_model_key: string
  force_layout_analysis: boolean
  layout_task_id: string
  status: "pending" | "running" | "succeeded" | "failed"
  layout_status: "pending" | "running" | "succeeded" | "failed"
  image_analysis_status: "pending" | "running" | "succeeded" | "failed"
  image_total_count: number
  image_succeeded_count: number
  image_failed_count: number
  reused: boolean
  markdown: string | null
  image_hashes: Record<string, string>
  image_items: ApiDocumentParsingImageItem[]
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export type ApiDocumentDetail = ApiDocument & {
  parsing_task: ApiDocumentParsingTask | null
}

export type CreateDocumentParsingTaskPayload = {
  document_id: string
  layout_model?: string | null
  image_model?: string | null
  force_layout_analysis?: boolean
}

export type UpdateDocumentPayload = {
  name: string
  description: string
}

export function getApiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
}

export function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
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

export async function listProjectDocuments(projectId: string, signal?: AbortSignal): Promise<ApiDocument[]> {
  const query = new URLSearchParams({
    project_id: projectId,
    limit: "200",
  })

  const response = await fetch(`${getApiBaseUrl()}/documents?${query.toString()}`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载项目文档失败（HTTP ${response.status}）`))
  }

  const data = (await response.json()) as ApiDocument[]
  return Array.isArray(data) ? data : []
}

export async function fetchDocument(documentId: string, signal?: AbortSignal): Promise<ApiDocumentDetail> {
  const response = await fetch(`${getApiBaseUrl()}/documents/${documentId}`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载文档详情失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiDocumentDetail
}

export async function updateDocument(documentId: string, payload: UpdateDocumentPayload): Promise<ApiDocument> {
  const response = await fetch(`${getApiBaseUrl()}/documents/${documentId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `保存文档失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiDocument
}

export async function createDocumentParsingTask(
  payload: CreateDocumentParsingTaskPayload
): Promise<ApiDocumentParsingTask> {
  const response = await fetch(`${getApiBaseUrl()}/document-parsing/tasks`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `创建解析任务失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiDocumentParsingTask
}

export async function fetchDocumentParsingTask(
  taskId: string,
  signal?: AbortSignal
): Promise<ApiDocumentParsingTask> {
  const response = await fetch(`${getApiBaseUrl()}/document-parsing/tasks/${taskId}`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载解析任务失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiDocumentParsingTask
}
