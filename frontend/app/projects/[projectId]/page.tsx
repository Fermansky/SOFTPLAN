"use client"

import Link from "next/link"
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

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
  const [project, setProject] = useState<ApiProject | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()

    async function fetchProjectDetail() {
      try {
        setLoading(true)
        setError(null)

        const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
        const res = await fetch(`${apiBase}/projects/${params.projectId}`, {
          method: "GET",
          signal: controller.signal,
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
    }

    fetchProjectDetail()

    return () => controller.abort()
  }, [params.projectId])

  const statusMeta = useMemo(
    () => (project ? getStatusMeta(project.status) : null),
    [project]
  )

  async function uploadProjectDocument(file: File) {
    try {
      setIsUploading(true)
      setUploadError(null)
      setUploadMessage(null)

      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
      const formData = new FormData()
      formData.append("project_id", params.projectId)
      formData.append("name", file.name)
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

      setUploadMessage(`上传成功：${file.name}`)
    } catch (uploadErr) {
      setUploadError(uploadErr instanceof Error ? uploadErr.message : "上传失败，请重试")
    } finally {
      setIsUploading(false)
    }
  }

  function handleSelectFileClick() {
    fileInputRef.current?.click()
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0]
    event.target.value = ""
    if (!selectedFile) return
    void uploadProjectDocument(selectedFile)
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-slate-900">项目详情</h1>
        <div className="flex items-center gap-2">
          <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileChange} />
          <Button onClick={handleSelectFileClick} disabled={loading || Boolean(error) || isUploading}>
            {isUploading ? "上传中..." : "上传文档"}
          </Button>
          <Button asChild variant="outline">
            <Link href="/">返回列表</Link>
          </Button>
        </div>
      </div>

      {uploadMessage ? <p className="mb-3 text-sm text-emerald-600">{uploadMessage}</p> : null}
      {uploadError ? <p className="mb-3 text-sm text-red-600">{uploadError}</p> : null}

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
      ) : null}
    </div>
  )
}
