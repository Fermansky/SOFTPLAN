"use client"

import { FormEvent, useEffect, useState } from "react"
import {
  Bot,
  CheckCircle2,
  CircleAlert,
  Loader2,
  Plus,
  RefreshCcw,
  ShieldCheck,
  Trash2,
} from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { formatDate } from "@/lib/documents"
import {
  activateLlmConfig,
  ApiLlmConfig,
  ApiLlmConfigListItem,
  createLlmConfig,
  deleteLlmConfig,
  fetchLlmConfig,
  listLlmConfigs,
  LlmConfigProvider,
  LlmConfigValidationResult,
  LlmValidationDepth,
  updateLlmConfig,
  validateLlmConfig,
} from "@/lib/llm-configs"
import { cn } from "@/lib/utils"

const PROVIDER_OPTIONS: Array<{ value: LlmConfigProvider; label: string; hint: string }> = [
  {
    value: "openai_compatible",
    label: "OpenAI Compatible",
    hint: "适用于 OpenAI 风格 API 的服务端点。",
  },
]

const VALIDATION_DEPTH_OPTIONS: Array<{ value: LlmValidationDepth; label: string }> = [
  { value: "strict", label: "严格校验" },
  { value: "basic", label: "基础校验" },
]

const SELECT_CLASS_NAME =
  "flex h-8 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none transition-all focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"

type FormState = {
  code: string
  name: string
  provider: LlmConfigProvider
  baseUrl: string
  apiKey: string
  defaultModel: string
  timeoutSeconds: string
  enabled: boolean
  createAndActivate: boolean
}

function createEmptyForm(): FormState {
  return {
    code: "",
    name: "",
    provider: "openai_compatible",
    baseUrl: "",
    apiKey: "",
    defaultModel: "",
    timeoutSeconds: "30",
    enabled: true,
    createAndActivate: false,
  }
}

function createFormFromConfig(config: ApiLlmConfig): FormState {
  return {
    code: config.code,
    name: config.name,
    provider: config.provider,
    baseUrl: config.base_url,
    apiKey: "",
    defaultModel: config.default_model,
    timeoutSeconds: String(config.timeout_seconds),
    enabled: config.enabled,
    createAndActivate: false,
  }
}

function ModelSettingsSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 xl:overflow-hidden">
      <Card className="shrink-0">
        <CardHeader>
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-4 w-72" />
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <div key={index} className="rounded-2xl border border-slate-200 p-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="mt-3 h-6 w-36" />
              <Skeleton className="mt-2 h-4 w-full" />
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)] xl:overflow-hidden">
        <Card className="shrink-0">
          <CardHeader>
            <Skeleton className="h-6 w-24" />
          </CardHeader>
          <CardContent className="min-h-0 flex-1 space-y-3 xl:overflow-y-auto xl:[scrollbar-gutter:stable] xl:pr-3 xl:pb-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="rounded-2xl border border-slate-200 p-4">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="mt-2 h-4 w-full" />
                <Skeleton className="mt-2 h-4 w-24" />
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="flex h-full min-h-0 flex-col gap-4 xl:overflow-hidden">
          <Card className="shrink-0">
            <CardHeader>
              <Skeleton className="h-6 w-32" />
              <Skeleton className="h-4 w-80" />
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="space-y-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ))}
            </CardContent>
            <CardFooter className="justify-end gap-2">
              <Skeleton className="h-8 w-24" />
              <Skeleton className="h-8 w-28" />
            </CardFooter>
          </Card>

          <Card className="shrink-0">
            <CardHeader>
              <Skeleton className="h-6 w-32" />
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="space-y-2">
                  <Skeleton className="h-4 w-20" />
                  <Skeleton className="h-4 w-full" />
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ active, enabled }: { active: boolean; enabled: boolean }) {
  if (active) {
    return <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100">当前激活</Badge>
  }

  if (enabled) {
    return <Badge className="bg-sky-100 text-sky-700 hover:bg-sky-100">已启用</Badge>
  }

  return <Badge className="bg-slate-100 text-slate-600 hover:bg-slate-100">已禁用</Badge>
}

function ValidationStatusCard({
  result,
  loading,
  currentConfigName,
}: {
  result: LlmConfigValidationResult | null
  loading: boolean
  currentConfigName: string | null
}) {
  const hasResult = Boolean(result)

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>最近一次校验</CardTitle>
            <CardDescription>
              {currentConfigName ? `记录当前选中配置“${currentConfigName}”的最近一次显式校验结果。` : "保存最近一次校验返回。"}
            </CardDescription>
          </div>
          {loading ? <Loader2 className="size-4 animate-spin text-slate-400" /> : null}
        </div>
      </CardHeader>
      <CardContent>
        {hasResult && result ? (
          <div className="space-y-4 text-sm text-slate-600">
            <div
              className={cn(
                "flex items-center gap-2 rounded-2xl border px-4 py-3",
                result.valid
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                  : "border-amber-200 bg-amber-50 text-amber-700"
              )}
            >
              {result.valid ? <CheckCircle2 className="size-4" /> : <CircleAlert className="size-4" />}
              <span>{result.valid ? "校验通过" : "校验未通过"}</span>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">阶段</p>
                <p className="mt-1 break-words text-slate-900">{result.stage}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">模型检查</p>
                <p className="mt-1 text-slate-900">{result.model_checked ? "已检查模型可调用性" : "仅基础可达性"}</p>
              </div>
              <div className="md:col-span-2">
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">规范化地址</p>
                <p className="mt-1 break-all text-slate-900">{result.normalized_base_url}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">延迟</p>
                <p className="mt-1 text-slate-900">{result.latency_ms !== null ? `${result.latency_ms} ms` : "--"}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">HTTP 状态</p>
                <p className="mt-1 text-slate-900">{result.http_status ?? "--"}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">错误代码</p>
                <p className="mt-1 break-words text-slate-900">{result.error_code ?? "--"}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-slate-500">错误信息</p>
                <p className="mt-1 break-words text-slate-900">{result.error_message ?? "--"}</p>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-sm text-slate-500">
            还没有执行过显式校验。保存或选择一个配置后，可以使用“校验配置”查看连通性与模型可调用性结果。
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default function ModelSettingsPage() {
  const [configs, setConfigs] = useState<ApiLlmConfigListItem[]>([])
  const [selectedConfigId, setSelectedConfigId] = useState<string | null>(null)
  const [mode, setMode] = useState<"create" | "edit">("create")
  const [detail, setDetail] = useState<ApiLlmConfig | null>(null)
  const [form, setForm] = useState<FormState>(() => createEmptyForm())

  const [bootstrapping, setBootstrapping] = useState(true)
  const [listRefreshing, setListRefreshing] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [formError, setFormError] = useState("")

  const [validationDepth, setValidationDepth] = useState<LlmValidationDepth>("strict")
  const [validationResult, setValidationResult] = useState<LlmConfigValidationResult | null>(null)
  const [validationForId, setValidationForId] = useState<string | null>(null)

  const [isSaving, setIsSaving] = useState(false)
  const [isValidating, setIsValidating] = useState(false)
  const [isActivating, setIsActivating] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  const activeConfig = configs.find((config) => config.is_active) ?? null
  const selectedListItem = selectedConfigId ? configs.find((config) => config.id === selectedConfigId) ?? null : null
  const isCreateMode = mode === "create"
  const currentValidationResult = validationForId === detail?.id ? validationResult : null

  async function loadConfigs(options?: {
    preferredId?: string | null
    silent?: boolean
    signal?: AbortSignal
    keepSelection?: boolean
  }) {
    const silent = options?.silent ?? false

    if (silent) {
      setListRefreshing(true)
    } else {
      setBootstrapping(true)
    }

    try {
      const data = await listLlmConfigs(options?.signal)
      setConfigs(data)
      setListError(null)

      if (data.length === 0) {
        setMode("create")
        setSelectedConfigId(null)
        setDetail(null)
        setDetailError(null)
        setValidationResult(null)
        setValidationForId(null)
        setForm(createEmptyForm())
        return
      }

      const preferredId = options?.preferredId
      const currentId = options?.keepSelection ? selectedConfigId : null
      const nextSelectedId =
        (preferredId && data.find((config) => config.id === preferredId)?.id) ??
        (currentId && data.find((config) => config.id === currentId)?.id) ??
        data.find((config) => config.is_active)?.id ??
        data[0]?.id ??
        null

      setMode("edit")
      setSelectedConfigId(nextSelectedId)
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        return
      }

      const message = error instanceof Error ? error.message : "加载模型配置列表失败"
      setListError(message)
      if (!silent) {
        setConfigs([])
      }
    } finally {
      if (silent) {
        setListRefreshing(false)
      } else {
        setBootstrapping(false)
      }
    }
  }

  async function loadDetail(configId: string, signal?: AbortSignal) {
    try {
      setDetailLoading(true)
      setDetailError(null)
      const data = await fetchLlmConfig(configId, signal)
      setDetail(data)
      setForm(createFormFromConfig(data))
      setFormError("")
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        return
      }

      const message = error instanceof Error ? error.message : "加载模型配置详情失败"
      setDetail(null)
      setDetailError(message)
    } finally {
      setDetailLoading(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    void loadConfigs({ signal: controller.signal })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (mode !== "edit" || !selectedConfigId) {
      return
    }

    const controller = new AbortController()
    void loadDetail(selectedConfigId, controller.signal)
    return () => controller.abort()
  }, [mode, selectedConfigId])

  function startCreateMode() {
    setMode("create")
    setSelectedConfigId(null)
    setDetail(null)
    setDetailError(null)
    setFormError("")
    setValidationResult(null)
    setValidationForId(null)
    setForm(createEmptyForm())
  }

  function selectConfig(configId: string) {
    setMode("edit")
    setSelectedConfigId(configId)
    setDetail(null)
    setDetailError(null)
    setFormError("")
    if (validationForId !== configId) {
      setValidationResult(null)
      setValidationForId(null)
    }
  }

  function updateForm<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function buildValidatedPayload() {
    const normalizedCode = form.code.trim()
    const normalizedName = form.name.trim()
    const normalizedBaseUrl = form.baseUrl.trim()
    const normalizedApiKey = form.apiKey.trim()
    const normalizedModel = form.defaultModel.trim()
    const timeoutSeconds = Number(form.timeoutSeconds)

    if (isCreateMode && !normalizedCode) {
      throw new Error("配置代号不能为空。")
    }
    if (!normalizedName) {
      throw new Error("配置名称不能为空。")
    }
    if (!normalizedBaseUrl) {
      throw new Error("基础地址不能为空。")
    }
    if (!normalizedModel) {
      throw new Error("默认模型不能为空。")
    }
    if (!Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) {
      throw new Error("超时时间必须是大于 0 的数字。")
    }
    if (isCreateMode && form.enabled && !normalizedApiKey) {
      throw new Error("启用配置时必须提供 API Key。")
    }
    if (!isCreateMode && form.enabled && !normalizedApiKey && !detail?.has_api_key) {
      throw new Error("当前配置尚未保存 API Key，启用前请先填写。")
    }
    if (!isCreateMode && detail?.is_active && !form.enabled) {
      throw new Error("当前激活配置不能直接禁用，请先激活其它配置。")
    }

    return {
      normalizedCode,
      normalizedName,
      normalizedBaseUrl,
      normalizedApiKey,
      normalizedModel,
      timeoutSeconds,
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    try {
      const payload = buildValidatedPayload()
      setFormError("")
      setIsSaving(true)

      if (isCreateMode) {
        const created = await createLlmConfig({
          code: payload.normalizedCode,
          name: payload.normalizedName,
          provider: form.provider,
          base_url: payload.normalizedBaseUrl,
          api_key: payload.normalizedApiKey,
          default_model: payload.normalizedModel,
          timeout_seconds: payload.timeoutSeconds,
          enabled: form.enabled,
          is_active: form.createAndActivate,
        })

        toast.success("创建成功", {
          description: `模型配置“${created.name}”已创建。`,
        })

        setValidationResult(null)
        setValidationForId(null)
        await loadConfigs({ preferredId: created.id, silent: true })
        return
      }

      if (!detail) {
        throw new Error("当前没有可编辑的配置详情。")
      }

      const updated = await updateLlmConfig(detail.id, {
        name: payload.normalizedName,
        provider: form.provider,
        base_url: payload.normalizedBaseUrl,
        default_model: payload.normalizedModel,
        timeout_seconds: payload.timeoutSeconds,
        enabled: form.enabled,
        ...(payload.normalizedApiKey ? { api_key: payload.normalizedApiKey } : {}),
      })

      setDetail(updated)
      setForm(createFormFromConfig(updated))
      toast.success("保存成功", {
        description: `模型配置“${updated.name}”已更新。`,
      })
      await loadConfigs({ preferredId: updated.id, silent: true, keepSelection: true })
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存模型配置失败，请重试。"
      setFormError(message)
      toast.error("保存失败", { description: message })
    } finally {
      setIsSaving(false)
    }
  }

  async function handleActivate() {
    if (!detail) {
      return
    }

    setIsActivating(true)
    setFormError("")

    try {
      const activated = await activateLlmConfig(detail.id)
      setDetail(activated)
      setForm(createFormFromConfig(activated))
      toast.success("已切换激活配置", {
        description: `当前激活模型已切换为“${activated.name}”。`,
      })
      await loadConfigs({ preferredId: activated.id, silent: true, keepSelection: true })
    } catch (error) {
      const message = error instanceof Error ? error.message : "激活模型配置失败，请重试。"
      setFormError(message)
      toast.error("激活失败", { description: message })
    } finally {
      setIsActivating(false)
    }
  }

  async function handleValidate() {
    if (!detail) {
      return
    }

    setIsValidating(true)
    setFormError("")

    try {
      const result = await validateLlmConfig(detail.id, validationDepth)
      setValidationResult(result)
      setValidationForId(detail.id)
      if (result.valid) {
        toast.success("校验通过", {
          description: "配置已通过当前深度的连通性校验。",
        })
      } else {
        toast.warning("校验未通过", {
          description: result.error_message ?? "当前配置已返回校验结果，但结果不理想，请查看失败阶段与原因。",
        })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "校验模型配置失败，请重试。"
      setFormError(message)
      toast.error("校验失败", { description: message })
    } finally {
      setIsValidating(false)
    }
  }

  async function handleDelete() {
    if (!detail || detail.is_active) {
      return
    }

    setIsDeleting(true)
    setFormError("")

    try {
      await deleteLlmConfig(detail.id)
      toast.success("删除成功", {
        description: `模型配置“${detail.name}”已删除。`,
      })
      setValidationResult(null)
      setValidationForId(null)
      await loadConfigs({ silent: true })
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除模型配置失败，请重试。"
      setFormError(message)
      toast.error("删除失败", { description: message })
    } finally {
      setIsDeleting(false)
    }
  }

  if (bootstrapping) {
    return <ModelSettingsSkeleton />
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 xl:overflow-hidden">
      <Card className="shrink-0 xl:m-1">
        <CardHeader className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <CardTitle>模型配置概览</CardTitle>
            <CardDescription>管理 LLM 配置列表、当前激活项以及显式校验结果。</CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => void loadConfigs({ silent: true, keepSelection: true })}>
              {listRefreshing ? <Loader2 className="size-4 animate-spin" /> : <RefreshCcw className="size-4" />}
              刷新列表
            </Button>
            <Button type="button" onClick={startCreateMode}>
              <Plus className="size-4" />
              新建配置
            </Button>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">当前激活</p>
            <p className="mt-3 text-base font-semibold text-slate-900">{activeConfig?.name ?? "暂无激活配置"}</p>
            <p className="mt-2 text-sm text-slate-500">{activeConfig ? `${activeConfig.code} · ${activeConfig.default_model}` : "创建并激活一个配置后，这里会显示当前生效模型。"}</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">配置数量</p>
            <p className="mt-3 text-base font-semibold text-slate-900">{configs.length}</p>
            <p className="mt-2 text-sm text-slate-500">包含已启用和已禁用的未删除配置。</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">最近更新</p>
            <p className="mt-3 text-base font-semibold text-slate-900">
              {activeConfig ? formatDate(activeConfig.updated_at) : "--"}
            </p>
            <p className="mt-2 text-sm text-slate-500">优先显示当前激活配置的更新时间。</p>
          </div>
        </CardContent>
      </Card>

      <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)] xl:overflow-hidden">
        <Card className="flex min-h-0 flex-col xl:my-3 xl:mx-1">
          <CardHeader>
            <CardTitle>配置列表</CardTitle>
            <CardDescription>默认优先选中当前激活配置，也可以切换到其它配置继续编辑。</CardDescription>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 space-y-3 xl:overflow-y-auto xl:[scrollbar-gutter:stable] xl:pr-3 xl:pb-3">
            {listError ? (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                {listError}
              </div>
            ) : null}

            {configs.length === 0 ? (
              <Empty className="min-h-[240px] border-slate-200 bg-slate-50/70">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <Bot className="size-4" />
                  </EmptyMedia>
                  <EmptyTitle>还没有模型配置</EmptyTitle>
                  <EmptyDescription>先创建一个配置，再在右侧完成详细设置、校验与激活。</EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <Button type="button" onClick={startCreateMode}>
                    <Plus className="size-4" />
                    新建第一个配置
                  </Button>
                </EmptyContent>
              </Empty>
            ) : (
              <div className="space-y-2">
                {configs.map((config) => {
                  const isSelected = !isCreateMode && selectedConfigId === config.id

                  return (
                    <button
                      key={config.id}
                      type="button"
                      onClick={() => selectConfig(config.id)}
                      className={cn(
                        "w-full rounded-2xl border px-4 py-3 text-left transition-colors",
                        isSelected
                          ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                          : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                      )}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{config.name}</p>
                          <p className={cn("mt-1 truncate text-xs", isSelected ? "text-white/75" : "text-slate-500")}>
                            {config.code}
                          </p>
                        </div>
                        <StatusBadge active={config.is_active} enabled={config.enabled} />
                      </div>
                      <div className={cn("mt-3 flex flex-wrap gap-2 text-xs", isSelected ? "text-white/80" : "text-slate-500")}>
                        <span className="rounded-full bg-black/5 px-2 py-1 dark:bg-white/5">{config.provider}</span>
                        <span className="rounded-full bg-black/5 px-2 py-1 dark:bg-white/5">{config.default_model}</span>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="min-h-0 xl:h-full">
          <div className="flex h-full min-h-0 flex-col xl:overflow-hidden">
            <div className="min-h-0 flex-1 xl:overflow-y-auto xl:[scrollbar-gutter:stable]">
              <div className="space-y-4 px-px xl:pr-3 xl:py-3">
          <Card className="shrink-0">
            <CardHeader>
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="space-y-1">
                  <CardTitle>{isCreateMode ? "新建模型配置" : detail?.name ?? selectedListItem?.name ?? "模型配置详情"}</CardTitle>
                  <CardDescription>
                    {isCreateMode
                      ? "填写基础连接信息，创建后即可继续校验、激活或修改。"
                      : "编辑当前模型配置的连接参数、默认模型与启用状态。"}
                  </CardDescription>
                </div>
                {!isCreateMode && detail ? (
                  <div className="flex flex-wrap gap-2">
                    <div className="min-w-[120px]">
                      <select
                        className={SELECT_CLASS_NAME}
                        value={validationDepth}
                        onChange={(event) => setValidationDepth(event.target.value as LlmValidationDepth)}
                        disabled={isValidating || isSaving || isActivating}
                        aria-label="选择校验深度"
                      >
                        {VALIDATION_DEPTH_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <Button type="button" variant="outline" onClick={handleValidate} disabled={isValidating || isSaving || isActivating}>
                      {isValidating ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
                      校验配置
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={handleActivate}
                      disabled={detail.is_active || !detail.enabled || isSaving || isValidating || isActivating}
                    >
                      {isActivating ? <Loader2 className="size-4 animate-spin" /> : <CheckCircle2 className="size-4" />}
                      {detail.is_active ? "当前已激活" : "设为激活"}
                    </Button>
                  </div>
                ) : null}
              </div>
            </CardHeader>

            {detailLoading && !isCreateMode ? (
              <CardContent className="grid gap-4 md:grid-cols-2">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div key={index} className="space-y-2">
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ))}
              </CardContent>
            ) : detailError && !isCreateMode ? (
              <CardContent>
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-700">
                  <p>{detailError}</p>
                  <div className="mt-3">
                    <Button type="button" variant="outline" onClick={() => selectedConfigId && void loadDetail(selectedConfigId)}>
                      重试加载
                    </Button>
                  </div>
                </div>
              </CardContent>
            ) : (
              <form onSubmit={handleSubmit}>
                <CardContent className="grid gap-4 md:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="llm-config-code">配置代号</Label>
                    <Input
                      id="llm-config-code"
                      value={form.code}
                      onChange={(event) => updateForm("code", event.target.value)}
                      placeholder="例如：primary-openai"
                      disabled={!isCreateMode || isSaving || isValidating || isActivating}
                      readOnly={!isCreateMode}
                      required
                    />
                    <p className="text-xs text-slate-500">创建后代号不可修改，用于稳定标识配置。</p>
                  </div>

                  <div className="grid gap-2">
                    <Label htmlFor="llm-config-name">配置名称</Label>
                    <Input
                      id="llm-config-name"
                      value={form.name}
                      onChange={(event) => updateForm("name", event.target.value)}
                      placeholder="例如：主模型服务"
                      disabled={isSaving || isValidating || isActivating}
                      required
                    />
                  </div>

                  <div className="grid gap-2">
                    <Label htmlFor="llm-config-provider">提供方</Label>
                    <select
                      id="llm-config-provider"
                      className={SELECT_CLASS_NAME}
                      value={form.provider}
                      onChange={(event) => updateForm("provider", event.target.value as LlmConfigProvider)}
                      disabled={isSaving || isValidating || isActivating}
                    >
                      {PROVIDER_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-slate-500">
                      {PROVIDER_OPTIONS.find((option) => option.value === form.provider)?.hint}
                    </p>
                  </div>

                  <div className="grid gap-2">
                    <Label htmlFor="llm-config-timeout">超时时间（秒）</Label>
                    <Input
                      id="llm-config-timeout"
                      type="number"
                      min="0.1"
                      step="0.1"
                      value={form.timeoutSeconds}
                      onChange={(event) => updateForm("timeoutSeconds", event.target.value)}
                      disabled={isSaving || isValidating || isActivating}
                      required
                    />
                  </div>

                  <div className="grid gap-2 md:col-span-2">
                    <Label htmlFor="llm-config-base-url">基础地址</Label>
                    <Input
                      id="llm-config-base-url"
                      value={form.baseUrl}
                      onChange={(event) => updateForm("baseUrl", event.target.value)}
                      placeholder="https://api.example.com/v1"
                      disabled={isSaving || isValidating || isActivating}
                      required
                    />
                  </div>

                  <div className="grid gap-2 md:col-span-2">
                    <Label htmlFor="llm-config-default-model">默认模型</Label>
                    <Input
                      id="llm-config-default-model"
                      value={form.defaultModel}
                      onChange={(event) => updateForm("defaultModel", event.target.value)}
                      placeholder="例如：gpt-4.1-mini"
                      disabled={isSaving || isValidating || isActivating}
                      required
                    />
                  </div>

                  <div className="grid gap-2 md:col-span-2">
                    <Label htmlFor="llm-config-api-key">API Key</Label>
                    <Input
                      id="llm-config-api-key"
                      type="password"
                      value={form.apiKey}
                      onChange={(event) => updateForm("apiKey", event.target.value)}
                      placeholder={isCreateMode ? "请输入 API Key" : "留空则保持当前已保存的 Key"}
                      disabled={isSaving || isValidating || isActivating}
                    />
                    {!isCreateMode && detail ? (
                      <p className="text-xs text-slate-500">
                        当前状态：{detail.has_api_key ? `已配置 ${detail.api_key_masked ?? "API Key"}` : "尚未保存 API Key"}
                      </p>
                    ) : (
                      <p className="text-xs text-slate-500">启用配置时通常需要提供可用的 API Key。</p>
                    )}
                  </div>

                  <div className="grid gap-3 md:col-span-2">
                    <div className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                      <Checkbox
                        id="llm-config-enabled"
                        checked={form.enabled}
                        onCheckedChange={(checked) => updateForm("enabled", checked === true)}
                        disabled={(detail?.is_active ?? false) || isSaving || isValidating || isActivating}
                      />
                      <div className="space-y-1">
                        <Label htmlFor="llm-config-enabled">启用配置</Label>
                        <p className="text-sm text-slate-500">
                          关闭后该配置不能被激活或作为默认配置解析。当前激活配置不可直接禁用。
                        </p>
                      </div>
                    </div>

                    {isCreateMode ? (
                      <div className="flex items-start gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                        <Checkbox
                          id="llm-config-activate-on-create"
                          checked={form.createAndActivate}
                          onCheckedChange={(checked) => updateForm("createAndActivate", checked === true)}
                          disabled={isSaving || isValidating || isActivating || !form.enabled}
                        />
                        <div className="space-y-1">
                          <Label htmlFor="llm-config-activate-on-create">创建后立即激活</Label>
                          <p className="text-sm text-slate-500">
                            激活前会走严格校验；若校验失败，创建请求会直接返回错误。
                          </p>
                        </div>
                      </div>
                    ) : null}
                  </div>

                  {!isCreateMode && detail ? (
                    <div className="md:col-span-2">
                      <Textarea
                        value={`配置 ID：${detail.id}\n创建时间：${formatDate(detail.created_at)}\n更新时间：${formatDate(detail.updated_at)}`}
                        readOnly
                        className="min-h-24 resize-none bg-slate-50 text-sm text-slate-600"
                      />
                    </div>
                  ) : null}

                  {formError ? <p className="md:col-span-2 text-sm text-destructive">{formError}</p> : null}
                </CardContent>
                <CardFooter className="flex flex-col items-stretch justify-between gap-3 border-t border-slate-200 bg-slate-50 sm:flex-row sm:items-center">
                  <div className="flex flex-wrap gap-2">
                    {!isCreateMode && detail ? (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            type="button"
                            variant="destructive"
                            disabled={detail.is_active || isDeleting || isSaving || isValidating || isActivating}
                          >
                            {isDeleting ? <Loader2 className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
                            删除配置
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent size="sm">
                          <AlertDialogHeader>
                            <AlertDialogTitle>删除当前配置？</AlertDialogTitle>
                            <AlertDialogDescription>
                              删除后将无法恢复“{detail.name}”。当前激活配置不允许删除，请先切换激活项。
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>取消</AlertDialogCancel>
                            <AlertDialogAction variant="destructive" onClick={handleDelete}>
                              确认删除
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    ) : (
                      <div className="text-sm text-slate-500">创建后即可继续校验、激活或删除该配置。</div>
                    )}
                  </div>

                  <div className="flex flex-wrap justify-end gap-2">
                    {isCreateMode ? (
                      configs.length > 0 ? (
                        <Button type="button" variant="outline" onClick={() => activeConfig ? selectConfig(activeConfig.id) : selectConfig(configs[0].id)}>
                          取消新建
                        </Button>
                      ) : null
                    ) : (
                      <Button type="button" variant="outline" onClick={startCreateMode}>
                        新建其它配置
                      </Button>
                    )}
                    <Button type="submit" disabled={isSaving || isValidating || isActivating || detailLoading}>
                      {isSaving ? <Loader2 className="size-4 animate-spin" /> : null}
                      {isCreateMode ? "创建配置" : "保存修改"}
                    </Button>
                  </div>
                </CardFooter>
              </form>
            )}
          </Card>

          <ValidationStatusCard
            result={currentValidationResult}
            loading={isValidating}
            currentConfigName={detail?.name ?? selectedListItem?.name ?? null}
          />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

