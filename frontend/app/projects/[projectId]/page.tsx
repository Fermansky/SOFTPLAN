"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { DetailPageHeader, DetailPageHeaderSkeleton } from "@/components/detail-page-header"
import { UploadDocumentDialog, type UploadDocumentPayload } from "@/components/upload-document-dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ApiDocument, formatDate, getApiBaseUrl, listProjectDocuments } from "@/lib/documents"
import { PAGE_CONTAINER_CLASS } from "@/lib/layout"

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

function DocumentsListSkeleton() {
  return (
    <>
      <div className="hidden md:block">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[46%]">文档</TableHead>
              <TableHead className="w-[18%]">软件</TableHead>
              <TableHead className="w-[20%]">时间</TableHead>
              <TableHead className="w-[16%]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Array.from({ length: 3 }).map((_, index) => (
              <TableRow key={index}>
                <TableCell>
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-44" />
                    <Skeleton className="h-3 w-3/4" />
                  </div>
                </TableCell>
                <TableCell>
                  <Skeleton className="h-4 w-full" />
                </TableCell>
                <TableCell>
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-28" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-2">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="grid gap-3 md:hidden">
        {Array.from({ length: 2 }).map((_, index) => (
          <div key={index} className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
            <div className="space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-2/3" />
            </div>
            <div className="mt-4 grid gap-2">
              <Skeleton className="h-4 w-28" />
              <Skeleton className="h-4 w-36" />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

function ProjectDetailSkeleton() {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>
            <Skeleton className="h-7 w-64" />
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 p-5 pt-0">
          <div className="space-y-2">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-4 w-4/5" />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-40" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            <Skeleton className="h-6 w-24" />
          </CardTitle>
        </CardHeader>
        <CardContent className="p-5 pt-0">
          <DocumentsListSkeleton />
        </CardContent>
      </Card>
    </div>
  )
}

export default function ProjectDetailPage({ params }: { params: { projectId: string } }) {
  const apiBase = getApiBaseUrl()

  const [project, setProject] = useState<ApiProject | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [documents, setDocuments] = useState<ApiDocument[]>([])
  const [documentsLoading, setDocumentsLoading] = useState(true)
  const [documentsError, setDocumentsError] = useState<string | null>(null)

  const [isUploading, setIsUploading] = useState(false)
  const [documentToDelete, setDocumentToDelete] = useState<ApiDocument | null>(null)
  const [isDeletingDocument, setIsDeletingDocument] = useState(false)
  const [deleteDocumentError, setDeleteDocumentError] = useState("")

  const fetchProjectDetail = useCallback(
    async (signal?: AbortSignal) => {
      try {
        setLoading(true)
        setError(null)

        const response = await fetch(`${apiBase}/projects/${params.projectId}`, {
          method: "GET",
          signal,
          headers: { Accept: "application/json" },
        })

        if (response.status === 404) {
          throw new Error("项目不存在")
        }
        if (!response.ok) {
          throw new Error(`加载项目详情失败（HTTP ${response.status}）`)
        }

        const data = (await response.json()) as ApiProject
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
        const data = await listProjectDocuments(params.projectId, signal)
        setDocuments(data)
      } catch (fetchError) {
        if ((fetchError as Error).name === "AbortError") return
        setDocuments([])
        setDocumentsError(fetchError instanceof Error ? fetchError.message : "加载项目文档失败")
      } finally {
        setDocumentsLoading(false)
      }
    },
    [params.projectId]
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

      const response = await fetch(`${apiBase}/documents/upload`, {
        method: "POST",
        body: formData,
      })

      if (!response.ok) {
        let message = `上传失败（HTTP ${response.status}）`
        try {
          const errorData = (await response.json()) as { detail?: string }
          if (errorData.detail) {
            message = errorData.detail
          }
        } catch {
          // Keep fallback message when response is not JSON.
        }
        throw new Error(message)
      }

      toast.success("上传成功", {
        description: `文档“${name}”已上传。`,
      })
      await fetchProjectDocuments()
    } catch (uploadError) {
      throw uploadError instanceof Error ? uploadError : new Error("上传失败，请重试")
    } finally {
      setIsUploading(false)
    }
  }

  async function deleteProjectDocument(documentId: string) {
    const response = await fetch(`${apiBase}/documents/${documentId}`, {
      method: "DELETE",
    })

    if (!response.ok) {
      let message = `删除失败（HTTP ${response.status}）`
      try {
        const errorData = (await response.json()) as { detail?: string }
        if (errorData.detail) {
          message = errorData.detail
        }
      } catch {
        // Keep fallback message when response is not JSON.
      }
      throw new Error(message)
    }
  }

  async function handleConfirmDeleteDocument() {
    const targetDocument = documentToDelete
    if (!targetDocument) return

    setIsDeletingDocument(true)
    setDeleteDocumentError("")

    try {
      await deleteProjectDocument(targetDocument.id)
      setDocuments((prev) => prev.filter((item) => item.id !== targetDocument.id))
      setDocumentToDelete(null)
      toast.success("删除成功", {
        description: `文档“${targetDocument.name || "--"}”已删除。`,
      })
    } catch (deleteError) {
      const errorMessage = deleteError instanceof Error ? deleteError.message : "删除文档失败。"
      setDeleteDocumentError(errorMessage)
      toast.error("删除失败", {
        description: errorMessage,
      })
    } finally {
      setIsDeletingDocument(false)
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
            { label: project?.name ?? "项目详情" },
          ]}
          title={project?.name ?? "项目详情"}
          description="查看项目基本信息、关联文档与后续操作入口。"
          actions={
            <>
              <UploadDocumentDialog
                onUploadDocument={uploadProjectDocument}
                disabled={loading || Boolean(error)}
                isUploading={isUploading}
              />
              <Button asChild variant="outline">
                <Link href="/">返回列表</Link>
              </Button>
            </>
          }
        />
      )}

      {loading ? <ProjectDetailSkeleton /> : null}

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
              {documentsLoading ? (
                <DocumentsListSkeleton />
              ) : documents.length === 0 ? (
                <Empty className="rounded-2xl border border-dashed border-slate-300 bg-slate-50">
                  <EmptyHeader>
                    <EmptyTitle>当前项目暂无文档</EmptyTitle>
                    <EmptyDescription>请先上传文档，后续可以在这里查看、编辑和下载。</EmptyDescription>
                  </EmptyHeader>
                  <EmptyContent>
                    <UploadDocumentDialog
                      onUploadDocument={uploadProjectDocument}
                      disabled={loading || Boolean(error)}
                      isUploading={isUploading}
                    />
                  </EmptyContent>
                </Empty>
              ) : (
                <>
                  <div className="hidden md:block">
                    <Table className="table-fixed">
                      <TableHeader>
                        <TableRow className="hover:bg-transparent">
                          <TableHead className="w-[46%]">文档</TableHead>
                          <TableHead className="w-[18%]">软件</TableHead>
                          <TableHead className="w-[20%]">时间</TableHead>
                          <TableHead className="w-[16%] text-right">操作</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {documents.map((item) => {
                          const detailHref = `/projects/${params.projectId}/documents/${item.id}`

                          return (
                            <TableRow key={item.id}>
                              <TableCell className="align-top whitespace-normal">
                                <div className="space-y-1 pr-4">
                                  <Link href={detailHref} className="font-medium text-slate-900 underline-offset-4 hover:text-sky-700 hover:underline">
                                    {item.name || "--"}
                                  </Link>
                                  <p className="text-sm break-words text-slate-500">{item.description || "--"}</p>
                                </div>
                              </TableCell>
                              <TableCell className="align-top text-slate-600">
                                <p className="truncate" title={item.software_id ?? "--"}>
                                  {item.software_id ?? "--"}
                                </p>
                              </TableCell>
                              <TableCell className="align-top whitespace-normal text-slate-600">
                                <div className="space-y-1">
                                  <p className="text-sm font-medium text-slate-900">更新于 {formatDate(item.updated_at)}</p>
                                  <p className="text-xs text-slate-500">创建于 {formatDate(item.created_at)}</p>
                                </div>
                              </TableCell>
                              <TableCell className="align-top">
                                <div className="flex flex-col items-stretch gap-2 lg:items-end">
                                  <Button asChild size="sm" variant="outline">
                                    <a href={`${apiBase}/documents/${item.id}/download`} target="_blank" rel="noreferrer">
                                      下载
                                    </a>
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="destructive"
                                    onClick={() => {
                                      setDeleteDocumentError("")
                                      setDocumentToDelete(item)
                                    }}
                                  >
                                    删除
                                  </Button>
                                </div>
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  </div>

                  <div className="grid gap-3 md:hidden">
                    {documents.map((item) => {
                      const detailHref = `/projects/${params.projectId}/documents/${item.id}`

                      return (
                        <div key={item.id} className="rounded-xl border border-slate-200 bg-slate-50/80 p-4 shadow-sm">
                          <div className="space-y-2">
                            <Link href={detailHref} className="block font-medium text-slate-900 underline-offset-4 hover:text-sky-700 hover:underline">
                              {item.name || "--"}
                            </Link>
                            <p className="text-sm break-words text-slate-500">{item.description || "--"}</p>
                          </div>

                          <div className="mt-4 grid gap-3 text-sm">
                            <div>
                              <p className="text-xs uppercase tracking-[0.08em] text-slate-400">软件</p>
                              <p className="mt-1 truncate text-slate-700" title={item.software_id ?? "--"}>
                                {item.software_id ?? "--"}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs uppercase tracking-[0.08em] text-slate-400">时间</p>
                              <p className="mt-1 text-slate-900">更新于 {formatDate(item.updated_at)}</p>
                              <p className="mt-1 text-xs text-slate-500">创建于 {formatDate(item.created_at)}</p>
                            </div>
                          </div>

                          <div className="mt-4 grid grid-cols-2 gap-2">
                            <Button className="w-auto" asChild size="sm" variant="outline">
                              <a href={`${apiBase}/documents/${item.id}/download`} target="_blank" rel="noreferrer">
                                下载
                              </a>
                            </Button>
                            <Button
                              className="w-auto"
                              size="sm"
                              variant="destructive"
                              onClick={() => {
                                setDeleteDocumentError("")
                                setDocumentToDelete(item)
                              }}
                            >
                              删除
                            </Button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}

      <AlertDialog
        open={Boolean(documentToDelete)}
        onOpenChange={(open) => {
          if (!open && !isDeletingDocument) {
            setDocumentToDelete(null)
            setDeleteDocumentError("")
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除文档</AlertDialogTitle>
            <AlertDialogDescription>
              {documentToDelete
                ? `确定要删除文档“${documentToDelete.name || "--"}”吗？该操作不可恢复。`
                : "确定要删除该文档吗？"}
            </AlertDialogDescription>
            {deleteDocumentError ? (
              <AlertDialogDescription className="text-destructive">{deleteDocumentError}</AlertDialogDescription>
            ) : null}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeletingDocument}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={isDeletingDocument}
              onClick={(event) => {
                event.preventDefault()
                void handleConfirmDeleteDocument()
              }}
            >
              {isDeletingDocument ? "删除中..." : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}



