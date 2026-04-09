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

export async function fetchDocument(documentId: string, signal?: AbortSignal): Promise<ApiDocument> {
  const response = await fetch(`${getApiBaseUrl()}/documents/${documentId}`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response, `加载文档详情失败（HTTP ${response.status}）`))
  }

  return (await response.json()) as ApiDocument
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
