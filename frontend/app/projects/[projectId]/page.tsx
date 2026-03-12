"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { UploadDocumentDialog, type UploadDocumentPayload } from "@/components/upload-document-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type ApiProjectStatus = "draft" | "analyzing" | "completed" | "archived"

type ApiProject = {
  id: string
  name: string
  description: string
  status: ApiProjectStatus
  current_version_id: string | null
  created_at: string
  updated_at: string
}

type ApiDocument = {
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

function getStatusMeta(status: ApiProjectStatus) {
  const map: Record<ApiProjectStatus, { label: string; className: string }> = {
    draft: { label: "草稿", className: "bg-slate-100 text-slate-800 hover:bg-slate-100" },
    analyzing: { label: "分析中", className: "bg-sky-100 text-sky-800 hover:bg-sky-100" },
    completed: { label: "已完成", className: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100" },
    archived: { label: "已归档", className: "bg-zinc-100 text-zinc-800 hover:bg-zinc-100" },
  }

  return map[status]
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

export default function ProjectDetailPage({ params }: { params: { projectId: string } }) {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"

  const [project, setProject] = useState<ApiProject | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [documents, setDocuments] = useState<ApiDocument[]>([])
  const [documentsLoading, setDocumentsLoading] = useState(true)
  const [documentsError, setDocumentsError] = useState<string | null>(null)

  const [isUploading, setIsUploading] = useState(false)

  const fetchProjectDetail = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setLoading(true)
        setError(null)

        const res = await fetch(`${apiBase}/projects/${params.projectId}`, {
          method: "GET",
          signal,
          headers: { Accept: "application/json" },
        })

        if (res.status === 404) {
          throw new Error("项目不存在")
        }
        if (!res.ok) {
          throw new Error(`加载项目详情失败（HTTP ${res.status}）`)
        }

        const data = (await res.json()) as ApiProject
        setProject(data)
      } catch (fetchError) {
        if ((fetchError as Error).name === "AbortError") return
        setError(fetchError instanceof Error ? fetchError.message : "加载项目详情失败")
      } finally {
        setLoading(false)
      }
    },
    [apiBase, params.projectId]
  )

  const fetchProjectDocuments = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setDocumentsLoading(true)
        setDocumentsError(null)

        const query = new URLSearchParams({
          project_id: params.projectId,
          limit: "200",
        })
        const res = await fetch(`${apiBase}/documents?${query.toString()}`, {
          method: "GET",
          signal,
          headers: { Accept: "application/json" },
        })

        if (!res.ok) {
          throw new Error(`加载项目文档失败（HTTP ${res.status}）`)
        }

        const data = (await res.json()) as ApiDocument[]
        setDocuments(Array.isArray(data) ? data : [])
      } catch (fetchError) {
        if ((fetchError as Error).name === "AbortError") return
        setDocuments([])
        setDocumentsError(fetchError instanceof Error ? fetchError.message : "加载项目文档失败")
      } finally {
        setDocumentsLoading(false)
      }
    },
    [apiBase, params.projectId]
  )

  useEffect(() => {
    const controller = new AbortController()

    void fetchProjectDetail(controller.signal)
    void fetchProjectDocuments(controller.signal)

    return () => controller.abort()
  }, [fetchProjectDetail, fetchProjectDocuments])

  const statusMeta = useMemo(() => (project ? getStatusMeta(project.status) : null), [project])

  async function uploadProjectDocument({ file, name, description }: UploadDocumentPayload) {
    try {
      setIsUploading(true)
      const formData = new FormData()
      formData.append("project_id", params.projectId)
      formData.append("name", name)
      formData.append("description", description)
      formData.append("file", file)

      const res = await fetch(`${apiBase}/documents/upload`, {
        method: "POST",
        body: formData,
      })

      if (!res.ok) {
        let message = `上传失败（HTTP ${res.status}）`
        try {
          const errorData = (await res.json()) as { detail?: string }
          if (errorData.detail) {
            message = errorData.detail
          }
        } catch {
          // Keep fallback message when response is not JSON.
        }
        throw new Error(message)
      }

      toast.success("上传成功", {
        description: `文档「${name}」已上传。`,
      })
      await fetchProjectDocuments()
    } catch (uploadErr) {
      throw uploadErr instanceof Error ? uploadErr : new Error("上传失败，请重试")
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-slate-900">项目详情</h1>
        <div className="flex items-center gap-2">
          <UploadDocumentDialog
            onUploadDocument={uploadProjectDocument}
            disabled={loading || Boolean(error)}
            isUploading={isUploading}
          />
          <Button asChild variant="outline">
            <Link href="/">返回列表</Link>
          </Button>
        </div>
      </div>

      {loading ? (
        <Card>
          <CardContent className="p-5 text-slate-600">正在加载项目详情...</CardContent>
        </Card>
      ) : null}

      {!loading && error ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            <p className="text-sm text-red-600">{error}</p>
            <p className="text-xs text-slate-500">项目 ID: {params.projectId}</p>
          </CardContent>
        </Card>
      ) : null}

      {!loading && !error && project ? (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 text-xl">
                <span>{project.name}</span>
                {statusMeta ? <Badge className={statusMeta.className}>{statusMeta.label}</Badge> : null}
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 p-5 pt-0">
              <div>
                <p className="text-sm text-slate-500">项目描述</p>
                <p className="mt-1 text-slate-900">{project.description || "--"}</p>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <p className="text-sm text-slate-500">项目 ID</p>
                  <p className="mt-1 break-all text-slate-900">{project.id}</p>
                </div>
                <div>
                  <p className="text-sm text-slate-500">当前版本 ID</p>
                  <p className="mt-1 break-all text-slate-900">{project.current_version_id ?? "--"}</p>
                </div>
                <div>
                  <p className="text-sm text-slate-500">创建时间</p>
                  <p className="mt-1 text-slate-900">{formatDate(project.created_at)}</p>
                </div>
                <div>
                  <p className="text-sm text-slate-500">更新时间</p>
                  <p className="mt-1 text-slate-900">{formatDate(project.updated_at)}</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>项目文档</CardTitle>
            </CardHeader>
            <CardContent className="p-5 pt-0">
              {documentsError ? <p className="mb-3 text-sm text-red-600">{documentsError}</p> : null}
              <Table className="min-w-[860px]">
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>文档名称</TableHead>
                    <TableHead>软件 ID</TableHead>
                    <TableHead>描述</TableHead>
                    <TableHead>创建时间</TableHead>
                    <TableHead>更新时间</TableHead>
                    <TableHead>操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {documentsLoading ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-slate-500">
                        正在加载文档...
                      </TableCell>
                    </TableRow>
                  ) : null}
                  {!documentsLoading && documents.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-slate-500">
                        当前项目暂无文档
                      </TableCell>
                    </TableRow>
                  ) : null}
                  {!documentsLoading
                    ? documents.map((item) => (
                        <TableRow key={item.id}>
                          <TableCell className="max-w-[260px] whitespace-normal break-words font-medium text-slate-900">
                            {item.name || "--"}
                          </TableCell>
                          <TableCell className="max-w-[220px] whitespace-normal break-all text-slate-600">
                            {item.software_id ?? "--"}
                          </TableCell>
                          <TableCell className="max-w-[320px] whitespace-normal break-words text-slate-600">
                            {item.description || "--"}
                          </TableCell>
                          <TableCell>{formatDate(item.created_at)}</TableCell>
                          <TableCell>{formatDate(item.updated_at)}</TableCell>
                          <TableCell>
                            <Button asChild size="sm" variant="outline">
                              <a href={`${apiBase}/documents/${item.id}/download`} target="_blank" rel="noreferrer">
                                下载
                              </a>
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))
                    : null}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  )
}
