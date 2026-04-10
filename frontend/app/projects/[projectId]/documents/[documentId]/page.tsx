"use client"

import Link from "next/link"
import { FormEvent, useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { DetailPageHeader, DetailPageHeaderSkeleton } from "@/components/detail-page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  ApiDocumentDetail,
  ApiDocumentParsingImageItem,
  ApiDocumentParsingTask,
  fetchDocument,
  formatDate,
  getApiBaseUrl,
  updateDocument,
} from "@/lib/documents"
import { PAGE_CONTAINER_CLASS } from "@/lib/layout"

function DocumentDetailSkeleton() {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>
            <Skeleton className="h-7 w-64" />
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 p-5 pt-0 sm:grid-cols-2">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="space-y-2">
              <Skeleton className="h-4 w-20" />
              <Skeleton className="h-4 w-full" />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            <Skeleton className="h-6 w-24" />
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 p-5 pt-0">
          <div className="space-y-2">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-8 w-full" />
          </div>
          <div className="space-y-2">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-32 w-full" />
          </div>
        </CardContent>
        <CardFooter className="justify-end gap-2">
          <Skeleton className="h-8 w-20" />
          <Skeleton className="h-8 w-24" />
        </CardFooter>
      </Card>
    </div>
  )
}

function DetailItem({ label, value, breakAll = false }: { label: string; value: string; breakAll?: boolean }) {
  return (
    <div>
      <p className="text-sm text-slate-500">{label}</p>
      <p className={`mt-1 text-slate-900 ${breakAll ? "break-all" : "break-words"}`}>{value}</p>
    </div>
  )
}

function formatOptionalDate(value: string | null | undefined) {
  return value ? formatDate(value) : "--"
}

function getTaskStatusMeta(status: ApiDocumentParsingTask["status"]) {
  const map: Record<ApiDocumentParsingTask["status"], { label: string; className: string }> = {
    pending: { label: "待处理", className: "bg-amber-100 text-amber-800 hover:bg-amber-100" },
    running: { label: "进行中", className: "bg-sky-100 text-sky-800 hover:bg-sky-100" },
    succeeded: { label: "已成功", className: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100" },
    failed: { label: "失败", className: "bg-rose-100 text-rose-800 hover:bg-rose-100" },
  }

  return map[status]
}

function getImageItemStatusMeta(status: ApiDocumentParsingImageItem["status"]) {
  const map: Record<ApiDocumentParsingImageItem["status"], { label: string; className: string }> = {
    pending: { label: "待处理", className: "bg-amber-100 text-amber-800 hover:bg-amber-100" },
    running: { label: "进行中", className: "bg-sky-100 text-sky-800 hover:bg-sky-100" },
    succeeded: { label: "已成功", className: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100" },
    failed: { label: "失败", className: "bg-rose-100 text-rose-800 hover:bg-rose-100" },
  }

  return map[status]
}

export default function DocumentDetailPage({ params }: { params: { projectId: string; documentId: string } }) {
  const apiBase = getApiBaseUrl()

  const [document, setDocument] = useState<ApiDocumentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [formError, setFormError] = useState("")
  const [isSaving, setIsSaving] = useState(false)

  const loadDocument = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setLoading(true)
        setError(null)

        const data = await fetchDocument(params.documentId, signal)
        if (data.project_id !== params.projectId) {
          throw new Error("文档不存在，或不属于当前项目")
        }

        setDocument(data)
        setName(data.name)
        setDescription(data.description)
      } catch (fetchError) {
        if ((fetchError as Error).name === "AbortError") return
        setDocument(null)
        setError(fetchError instanceof Error ? fetchError.message : "加载文档详情失败")
      } finally {
        setLoading(false)
      }
    },
    [params.documentId, params.projectId]
  )

  useEffect(() => {
    const controller = new AbortController()
    void loadDocument(controller.signal)
    return () => controller.abort()
  }, [loadDocument])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    const normalizedName = name.trim()
    if (!normalizedName) {
      setFormError("文档名称不能为空。")
      return
    }

    setFormError("")
    setIsSaving(true)

    try {
      const updated = await updateDocument(params.documentId, {
        name: normalizedName,
        description: description.trim(),
      })

      if (updated.project_id !== params.projectId) {
        throw new Error("保存结果异常，文档不属于当前项目")
      }

      setDocument((current) => ({
        ...updated,
        parsing_task: current?.parsing_task ?? null,
      }))
      setName(updated.name)
      setDescription(updated.description)
      toast.success("保存成功", {
        description: `文档“${updated.name}”已更新。`,
      })
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "保存文档失败，请重试。"
      setFormError(message)
      toast.error("保存失败", {
        description: message,
      })
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className={PAGE_CONTAINER_CLASS}>
      {loading ? (
        <DetailPageHeaderSkeleton />
      ) : (
        <DetailPageHeader
          items={[
            { label: "首页", href: "/" },
            { label: "项目详情", href: `/projects/${params.projectId}` },
            { label: document?.name || "文档详情" },
          ]}
          title={document?.name ?? "文档详情"}
          description="查看文档基础信息，并在当前项目下修改名称和描述。"
          actions={
            <>
              {document ? (
                <Button asChild variant="outline">
                  <a href={`${apiBase}/documents/${document.id}/download`} target="_blank" rel="noreferrer">
                    下载文档
                  </a>
                </Button>
              ) : null}
              <Button asChild variant="outline">
                <Link href={`/projects/${params.projectId}`}>返回项目</Link>
              </Button>
            </>
          }
        />
      )}

      {loading ? <DocumentDetailSkeleton /> : null}

      {!loading && error ? (
        <Card>
          <CardContent className="space-y-3 p-5">
            <p className="text-sm text-red-600">{error}</p>
            <p className="text-xs text-slate-500">项目 ID: {params.projectId}</p>
            <p className="text-xs text-slate-500">文档 ID: {params.documentId}</p>
          </CardContent>
        </Card>
      ) : null}

      {!loading && !error && document ? (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{document.name || "未命名文档"}</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 p-5 pt-0 sm:grid-cols-2">
              <DetailItem label="文档 ID" value={document.id} breakAll />
              <DetailItem label="文件 ID" value={document.file_id ?? "--"} breakAll />
              <DetailItem label="项目 ID" value={document.project_id} breakAll />
              <DetailItem label="软件 ID" value={document.software_id ?? "--"} breakAll />
              <DetailItem label="创建时间" value={formatDate(document.created_at)} />
              <DetailItem label="更新时间" value={formatDate(document.updated_at)} />
              <div className="sm:col-span-2">
                <p className="text-sm text-slate-500">当前描述</p>
                <p className="mt-1 whitespace-pre-wrap break-words text-slate-900">{document.description || "--"}</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>解析任务</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 p-5 pt-0">
              {document.parsing_task ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={getTaskStatusMeta(document.parsing_task.status).className}>
                      {getTaskStatusMeta(document.parsing_task.status).label}
                    </Badge>
                    <p className="text-sm text-slate-500">
                      默认关联任务会优先展示最新任务；若最新任务失败，则回退到最近一次成功任务。
                    </p>
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <DetailItem label="任务 ID" value={document.parsing_task.id} breakAll />
                    <DetailItem label="布局任务 ID" value={document.parsing_task.layout_task_id} breakAll />
                    <DetailItem label="任务状态" value={document.parsing_task.status} />
                    <DetailItem label="布局状态" value={document.parsing_task.layout_status} />
                    <DetailItem label="图像分析状态" value={document.parsing_task.image_analysis_status} />
                    <DetailItem
                      label="图像统计"
                      value={`${document.parsing_task.image_succeeded_count}/${document.parsing_task.image_total_count} 成功，${document.parsing_task.image_failed_count} 失败`}
                    />
                    <DetailItem label="布局模型" value={document.parsing_task.target_layout_model} />
                    <DetailItem label="图像模型" value={document.parsing_task.target_image_model ?? "--"} />
                    <DetailItem label="创建时间" value={formatDate(document.parsing_task.created_at)} />
                    <DetailItem label="开始时间" value={formatOptionalDate(document.parsing_task.started_at)} />
                    <DetailItem label="完成时间" value={formatOptionalDate(document.parsing_task.finished_at)} />
                    <DetailItem label="更新时间" value={formatDate(document.parsing_task.updated_at)} />
                  </div>

                  <div>
                    <p className="text-sm text-slate-500">任务错误</p>
                    <p className="mt-1 whitespace-pre-wrap break-words text-slate-900">
                      {document.parsing_task.error_message || "--"}
                    </p>
                  </div>

                  <div>
                    <p className="text-sm text-slate-500">Markdown 结果</p>
                    <pre className="mt-1 max-h-72 overflow-auto rounded-md bg-slate-950 p-4 text-sm leading-6 whitespace-pre-wrap break-words text-slate-100">
                      {document.parsing_task.markdown || "--"}
                    </pre>
                  </div>

                  <div className="space-y-3">
                    <p className="text-sm text-slate-500">图像语义结果</p>
                    {document.parsing_task.image_items.length ? (
                      document.parsing_task.image_items.map((item) => (
                        <div key={item.id} className="rounded-lg border border-slate-200 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="font-medium text-slate-900">{item.source_key}</p>
                            <Badge className={getImageItemStatusMeta(item.status).className}>
                              {getImageItemStatusMeta(item.status).label}
                            </Badge>
                          </div>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            <DetailItem label="提取图片 ID" value={String(item.extracted_image_id)} />
                            <DetailItem label="结果来源" value={item.result_source ?? "--"} />
                            <DetailItem label="语义任务 ID" value={item.semantic_task_id ?? "--"} breakAll />
                            <DetailItem label="文件哈希" value={item.file_hash} breakAll />
                          </div>
                          <div className="mt-3">
                            <p className="text-sm text-slate-500">语义描述</p>
                            <p className="mt-1 whitespace-pre-wrap break-words text-slate-900">
                              {item.semantic?.description || "--"}
                            </p>
                          </div>
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            <DetailItem label="结果模型" value={item.semantic?.result_model ?? "--"} />
                            <DetailItem label="语义更新时间" value={formatOptionalDate(item.semantic?.updated_at)} />
                          </div>
                          {item.error_message ? <p className="mt-3 text-sm text-destructive">{item.error_message}</p> : null}
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-slate-500">当前任务还没有图像语义结果。</p>
                    )}
                  </div>
                </>
              ) : (
                <p className="text-sm text-slate-500">暂无解析任务。</p>
              )}
            </CardContent>
          </Card>

          <Card className="pb-0">
            <CardHeader>
              <CardTitle>编辑文档</CardTitle>
            </CardHeader>
            <form onSubmit={handleSubmit}>
              <CardContent className="grid gap-4 p-5 pt-0">
                <div className="grid gap-2">
                  <label htmlFor="document-name" className="text-sm font-medium text-slate-700">
                    文档名称
                  </label>
                  <Input
                    id="document-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="请输入文档名称"
                    maxLength={255}
                    disabled={isSaving}
                    required
                  />
                </div>

                <div className="grid gap-2">
                  <label htmlFor="document-description" className="text-sm font-medium text-slate-700">
                    文档描述
                  </label>
                  <Textarea
                    id="document-description"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="可选，补充文档用途或背景"
                    disabled={isSaving}
                  />
                </div>

                {formError ? <p className="text-sm text-destructive">{formError}</p> : null}
              </CardContent>
              <CardFooter className="justify-end gap-2">
                <Button asChild variant="outline">
                  <Link href={`/projects/${params.projectId}`}>返回项目</Link>
                </Button>
                <Button type="submit" disabled={isSaving}>
                  {isSaving ? "保存中..." : "保存修改"}
                </Button>
              </CardFooter>
            </form>
          </Card>
        </div>
      ) : null}
    </div>
  )
}


