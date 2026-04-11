"use client"

import Link from "next/link"
import { FormEvent, useCallback, useEffect, useState } from "react"
import { toast } from "sonner"

import { DetailPageHeader, DetailPageHeaderSkeleton } from "@/components/detail-page-header"
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
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  ApiDocumentDetail,
  ApiDocumentParsingTask,
  CreateDocumentParsingTaskPayload,
  createDocumentParsingTask,
  fetchDocument,
  fetchDocumentParsingTask,
  formatDate,
  getApiBaseUrl,
  updateDocument,
} from "@/lib/documents"
import { ApiLlmConfigListItem, listLlmConfigs } from "@/lib/llm-configs"
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
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-sm font-medium text-slate-900 ${breakAll ? "break-all" : "break-words"}`}>{value}</p>
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

function SecondaryMetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-medium text-slate-900">{value}</p>
    </div>
  )
}

function isParsingActive(status: ApiDocumentParsingTask["status"] | null | undefined) {
  return status === "pending" || status === "running"
}

const SELECT_CLASS_NAME =
  "flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none transition-all focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50"

function formatParsingLlmConfigOptionLabel(config: ApiLlmConfigListItem) {
  return `${config.name} (${config.code} / ${config.default_model})`
}

export default function DocumentDetailPage({ params }: { params: { projectId: string; documentId: string } }) {
  const apiBase = getApiBaseUrl()

  const [document, setDocument] = useState<ApiDocumentDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [isParsingDialogOpen, setIsParsingDialogOpen] = useState(false)
  const [isOverwriteConfirmOpen, setIsOverwriteConfirmOpen] = useState(false)

  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [formError, setFormError] = useState("")
  const [isSaving, setIsSaving] = useState(false)
  const [parsingImageModel, setParsingImageModel] = useState("")
  const [parsingImageLlmConfigId, setParsingImageLlmConfigId] = useState("")
  const [forceLayoutAnalysis, setForceLayoutAnalysis] = useState(false)
  const [forceImageSemanticRecognition, setForceImageSemanticRecognition] = useState(false)
  const [parsingLlmConfigs, setParsingLlmConfigs] = useState<ApiLlmConfigListItem[]>([])
  const [isParsingLlmConfigsLoading, setIsParsingLlmConfigsLoading] = useState(false)
  const [parsingLlmConfigsError, setParsingLlmConfigsError] = useState("")
  const [parsingFormError, setParsingFormError] = useState("")
  const [isStartingParsing, setIsStartingParsing] = useState(false)

  const enabledParsingLlmConfigs = parsingLlmConfigs.filter((config) => config.enabled)
  const selectedParsingLlmConfig =
    enabledParsingLlmConfigs.find((config) => config.id === parsingImageLlmConfigId) ?? null
  const selectedTaskParsingLlmConfig =
    document?.parsing_task?.image_llm_config_id === parsingImageLlmConfigId ? document.parsing_task : null
  const showSelectedConfigFallbackOption = Boolean(
    parsingImageLlmConfigId && !selectedParsingLlmConfig && selectedTaskParsingLlmConfig?.image_llm_config_id
  )

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

  useEffect(() => {
    const task = document?.parsing_task
    if (!task || !isParsingActive(task.status)) {
      return
    }

    const taskId = task.id
    let cancelled = false
    let inFlight = false
    let lastStatus: ApiDocumentParsingTask["status"] = task.status
    const controller = new AbortController()
    const intervalId = window.setInterval(() => {
      void pollTask()
    }, 2000)

    async function pollTask() {
      if (cancelled || inFlight) {
        return
      }

      inFlight = true
      try {
        const latestTask = await fetchDocumentParsingTask(taskId, controller.signal)
        if (cancelled) {
          return
        }

        setDocument((current) => (current ? { ...current, parsing_task: latestTask } : current))

        const reachedTerminal =
          isParsingActive(lastStatus) && (latestTask.status === "succeeded" || latestTask.status === "failed")
        lastStatus = latestTask.status

        if (!reachedTerminal) {
          return
        }

        window.clearInterval(intervalId)

        if (latestTask.status === "succeeded") {
          toast.success("文档解析完成", {
            description: "解析结果已更新。",
          })
        } else {
          toast.error("文档解析失败", {
            description: latestTask.error_message || "任务执行失败，请稍后重试。",
          })
        }

        const syncedDocument = await fetchDocument(params.documentId)
        if (cancelled || syncedDocument.project_id !== params.projectId) {
          return
        }

        setDocument(syncedDocument)
      } catch (pollError) {
        if ((pollError as Error).name === "AbortError") {
          return
        }
      } finally {
        inFlight = false
      }
    }

    void pollTask()

    return () => {
      cancelled = true
      controller.abort()
      window.clearInterval(intervalId)
    }
  }, [document?.parsing_task?.id, document?.parsing_task?.status, params.documentId, params.projectId])

  useEffect(() => {
    if (!isParsingDialogOpen) {
      return
    }

    const controller = new AbortController()

    async function loadParsingLlmConfigs() {
      try {
        setIsParsingLlmConfigsLoading(true)
        const configs = await listLlmConfigs(controller.signal)
        setParsingLlmConfigs(configs)
        setParsingLlmConfigsError("")
      } catch (loadError) {
        if ((loadError as Error).name === "AbortError") {
          return
        }

        const message = loadError instanceof Error ? loadError.message : "加载模型配置列表失败"
        setParsingLlmConfigs([])
        setParsingLlmConfigsError(message)
      } finally {
        setIsParsingLlmConfigsLoading(false)
      }
    }

    void loadParsingLlmConfigs()

    return () => controller.abort()
  }, [isParsingDialogOpen])

  function openEditDialog() {
    if (!document) return
    setName(document.name)
    setDescription(document.description)
    setFormError("")
    setIsEditDialogOpen(true)
  }

  function handleEditDialogOpenChange(nextOpen: boolean) {
    setIsEditDialogOpen(nextOpen)
    if (!nextOpen) {
      setFormError("")
    }
  }

  function openParsingDialog() {
    setParsingImageModel(document?.parsing_task?.requested_image_model ?? "")
    setParsingImageLlmConfigId(document?.parsing_task?.image_llm_config_id ?? "")
    setForceLayoutAnalysis(forceLayoutAnalysis ?? false)
    setForceImageSemanticRecognition(forceImageSemanticRecognition ?? false)
    setParsingFormError("")
    setIsParsingDialogOpen(true)
  }

  function handleParsingDialogOpenChange(nextOpen: boolean) {
    setIsParsingDialogOpen(nextOpen)
    if (!nextOpen) {
      setParsingFormError("")
      setIsOverwriteConfirmOpen(false)
    }
  }

  async function startDocumentParsing(forceLayoutAnalysis: boolean) {
    const normalizedImageModel = parsingImageModel.trim()
    const normalizedImageLlmConfigId = parsingImageLlmConfigId.trim()
    const payload: CreateDocumentParsingTaskPayload = {
      document_id: params.documentId,
      layout_model: "marker",
      image_model: normalizedImageModel || null,
      image_llm_config_id: normalizedImageLlmConfigId || null,
      force_layout_analysis: forceLayoutAnalysis,
      force_image_semantic_recognition: forceImageSemanticRecognition,
    }

    setParsingFormError("")
    setIsStartingParsing(true)

    try {
      const createdTask = await createDocumentParsingTask(payload)
      setDocument((current) => (current ? { ...current, parsing_task: createdTask } : current))
      setIsParsingDialogOpen(false)
      setIsOverwriteConfirmOpen(false)

      if (isParsingActive(createdTask.status)) {
        toast.success(createdTask.reused ? "已连接解析任务" : "已开始解析", {
          description: createdTask.reused ? "将继续跟踪现有解析任务进度。" : "正在处理文档，请稍候。",
        })
      } else if (createdTask.status === "succeeded") {
        toast.success("解析结果已就绪", {
          description: "已获得最新的文档解析结果。",
        })
      } else if (createdTask.status === "failed") {
        toast.error("解析任务失败", {
          description: createdTask.error_message || "任务创建后执行失败。",
        })
      }
    } catch (taskCreateError) {
      const message = taskCreateError instanceof Error ? taskCreateError.message : "创建解析任务失败，请重试。"
      setParsingFormError(message)
    } finally {
      setIsStartingParsing(false)
    }
  }

  async function handleParsingSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (forceLayoutAnalysis && document?.parsing_task?.status === "succeeded") {
      setIsOverwriteConfirmOpen(true)
      return
    }

    await startDocumentParsing(forceLayoutAnalysis)
  }

  async function handleEditSubmit(event: FormEvent<HTMLFormElement>) {
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
      setIsEditDialogOpen(false)
      setFormError("")
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

  const isCurrentParsingActive = isParsingActive(document?.parsing_task?.status)

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
                <Button type="button" onClick={openParsingDialog} disabled={isCurrentParsingActive}>
                  {isCurrentParsingActive ? "解析中..." : "开始解析"}
                </Button>
              ) : null}
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
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.95fr)]">
            <Card className="border-slate-200 bg-white/90 shadow-sm">
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <CardTitle className="text-xl text-slate-950">{document.name || "未命名文档"}</CardTitle>
                  <Button type="button" variant="outline" onClick={openEditDialog}>
                    编辑文档
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-5 p-5 pt-0">
                <div className="rounded-2xl border border-slate-200 bg-slate-50/80 p-4">
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-500">文档摘要</p>
                  <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-slate-700">
                    {document.description || "暂无描述"}
                  </p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <SecondaryMetaItem label="创建时间" value={formatDate(document.created_at)} />
                  <SecondaryMetaItem label="更新时间" value={formatDate(document.updated_at)} />
                </div>

                <div className="grid gap-3 text-xs text-slate-500 sm:grid-cols-2">
                  <DetailItem label="文档 ID" value={document.id} breakAll />
                  <DetailItem label="文件 ID" value={document.file_id ?? "--"} breakAll />
                  <DetailItem label="项目 ID" value={document.project_id} breakAll />
                  <DetailItem label="软件 ID" value={document.software_id ?? "--"} breakAll />
                </div>
              </CardContent>
            </Card>

            <Card className="border-slate-200 bg-white/90 shadow-sm">
              <CardHeader className="pb-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <CardTitle className="text-lg text-slate-950">解析任务</CardTitle>
                    <p className="mt-1 text-sm text-slate-500">显示默认关联任务的最新状态与关键时间。</p>
                  </div>
                  {document.parsing_task ? (
                    <Badge className={getTaskStatusMeta(document.parsing_task.status).className}>
                      {getTaskStatusMeta(document.parsing_task.status).label}
                    </Badge>
                  ) : null}
                </div>
              </CardHeader>
              <CardContent className="p-5 pt-0">
                {document.parsing_task ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    <SecondaryMetaItem label="创建时间" value={formatDate(document.parsing_task.created_at)} />
                    <SecondaryMetaItem label="开始时间" value={formatOptionalDate(document.parsing_task.started_at)} />
                    <SecondaryMetaItem label="完成时间" value={formatOptionalDate(document.parsing_task.finished_at)} />
                    <SecondaryMetaItem label="更新时间" value={formatDate(document.parsing_task.updated_at)} />
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 p-4">
                    <p className="text-sm font-medium text-slate-700">暂无解析任务</p>
                    <p className="mt-1 text-sm text-slate-500">当前文档还没有可展示的默认解析任务状态。</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      ) : null}

      <Dialog open={isEditDialogOpen} onOpenChange={handleEditDialogOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <form onSubmit={handleEditSubmit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>编辑文档</DialogTitle>
              <DialogDescription>修改文档名称和描述，保存后会立即同步到当前页面。</DialogDescription>
            </DialogHeader>

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

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleEditDialogOpenChange(false)} disabled={isSaving}>
                取消
              </Button>
              <Button type="submit" disabled={isSaving}>
                {isSaving ? "保存中..." : "保存修改"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={isParsingDialogOpen} onOpenChange={handleParsingDialogOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <form onSubmit={handleParsingSubmit} className="grid gap-4">
            <DialogHeader>
              <DialogTitle>新建解析任务</DialogTitle>
              <DialogDescription>创建后会开始处理当前文档，并在任务运行期间自动更新状态。</DialogDescription>
            </DialogHeader>

            <div className="grid gap-2">
              <label htmlFor="document-layout-model" className="text-sm font-medium text-slate-700">
                版面模型
              </label>
              <Input id="document-layout-model" value="marker" disabled readOnly />
              <p className="text-xs text-slate-500">当前仅支持 `marker` 作为版面分析模型。</p>
            </div>

            <div className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
              <Checkbox
                id="document-force-layout-analysis"
                checked={forceLayoutAnalysis}
                onCheckedChange={(checked) => setForceLayoutAnalysis(checked === true)}
                disabled={isStartingParsing}
              />
              <div className="space-y-1">
                <label htmlFor="document-force-layout-analysis" className="text-sm font-medium text-slate-700">
                  强制重新执行版面分析
                </label>
                <p className="text-sm text-slate-500">
                  未勾选时会尽量复用已有版面分析结果；勾选后会重新解析文档版面，并刷新当前解析链路。
                </p>
              </div>
            </div>

            <div className="grid gap-2">
              <label htmlFor="document-image-model" className="text-sm font-medium text-slate-700">
                图像模型
              </label>
              <Input
                id="document-image-model"
                value={parsingImageModel}
                onChange={(event) => setParsingImageModel(event.target.value)}
                placeholder="可选，留空则使用系统默认模型"
                disabled={isStartingParsing}
              />
              <p className="text-xs text-slate-500">该参数用于控制图片语义分析，留空会使用默认模型配置。</p>
            </div>

            <div className="grid gap-2">
              <label htmlFor="document-image-llm-config" className="text-sm font-medium text-slate-700">
                图片语义 LLM 配置
              </label>
              <select
                id="document-image-llm-config"
                className={SELECT_CLASS_NAME}
                value={parsingImageLlmConfigId}
                onChange={(event) => setParsingImageLlmConfigId(event.target.value)}
                disabled={isStartingParsing || isParsingLlmConfigsLoading}
              >
                <option value="">当前激活配置</option>
                {enabledParsingLlmConfigs.map((config) => (
                  <option key={config.id} value={config.id}>
                    {formatParsingLlmConfigOptionLabel(config)}
                  </option>
                ))}
                {showSelectedConfigFallbackOption ? (
                  <option value={parsingImageLlmConfigId}>
                    {selectedTaskParsingLlmConfig?.image_llm_config_code
                      ? `当前任务配置（${selectedTaskParsingLlmConfig.image_llm_config_code}）`
                      : "当前任务配置"}
                  </option>
                ) : null}
              </select>
              {isParsingLlmConfigsLoading ? (
                <p className="text-xs text-slate-500">正在加载可用模型配置...</p>
              ) : parsingLlmConfigsError ? (
                <p className="text-xs text-amber-600">
                  {parsingLlmConfigsError}。仍可继续创建任务并使用当前激活配置。
                </p>
              ) : enabledParsingLlmConfigs.length === 0 ? (
                <p className="text-xs text-slate-500">当前没有可选的已启用配置，继续提交将使用当前激活配置。</p>
              ) : (
                <p className="text-xs text-slate-500">留空表示使用当前激活配置，也可以为本次图片语义分析绑定指定配置。</p>
              )}
            </div>

            <div className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
              <Checkbox
                id="document-force-image-semantic-recognition"
                checked={forceImageSemanticRecognition}
                onCheckedChange={(checked) => setForceImageSemanticRecognition(checked === true)}
                disabled={isStartingParsing}
              />
              <div className="space-y-1">
                <label
                  htmlFor="document-force-image-semantic-recognition"
                  className="text-sm font-medium text-slate-700"
                >
                  强制重新识别图片语义
                </label>
                <p className="text-sm text-slate-500">
                  未勾选时允许复用已有图片语义快照或任务；勾选后会对当前文档图片重新发起语义识别。
                </p>
              </div>
            </div>

            {parsingFormError ? <p className="text-sm text-destructive">{parsingFormError}</p> : null}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => handleParsingDialogOpenChange(false)}
                disabled={isStartingParsing}
              >
                取消
              </Button>
              <Button type="submit" disabled={isStartingParsing}>
                {isStartingParsing ? "创建中..." : "开始解析"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog open={isOverwriteConfirmOpen} onOpenChange={setIsOverwriteConfirmOpen}>
        <AlertDialogContent size="sm">
          <AlertDialogHeader>
            <AlertDialogTitle>覆盖现有解析结果？</AlertDialogTitle>
            <AlertDialogDescription>
              当前文档已经有成功的解析结果。继续后会重新发起解析任务，并在完成后覆盖当前展示的结果状态。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isStartingParsing}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void startDocumentParsing(true)} disabled={isStartingParsing}>
              {isStartingParsing ? "创建中..." : "确认覆盖并解析"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
