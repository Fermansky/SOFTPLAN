"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { FolderOpen, Search } from "lucide-react"

import { CreateProjectDialog, type CreateProjectPayload } from "@/components/create-project-dialog"
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
import { Alert, AlertAction, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

type ApiProjectStatus = "draft" | "analyzing" | "completed" | "archived"
type ProjectStatus = ApiProjectStatus | "parsing" | "pending_confirm" | "done"

type ApiProject = {
  id: string
  name: string
  description: string
  status: ApiProjectStatus
  current_version_id: string | null
  created_at: string
  updated_at: string
}

type ProjectItem = {
  id: string
  name: string
  description: string
  status: ProjectStatus
  version: string
  fp: number | null
  estimatedCost: number | null
  lastUpdated: string
  reportGeneratedAt?: string | null
}

type DeleteAlert = {
  variant: "default" | "destructive"
  title: string
  description: string
}

const MOCK_PROJECTS: ProjectItem[] = [
  {
    id: "p-1001",
    name: "智慧校园排课系统",
    description: "课程排课与教师工作量核算模块整合",
    status: "pending_confirm",
    version: "V1.2",
    fp: 126,
    estimatedCost: 286000,
    lastUpdated: "2026-03-06T10:30:00+08:00",
    reportGeneratedAt: "2026-03-05T17:10:00+08:00",
  },
  {
    id: "p-1002",
    name: "科研数据中台",
    description: "多源数据接入、清洗与统一检索",
    status: "analyzing",
    version: "V0.9",
    fp: 214,
    estimatedCost: null,
    lastUpdated: "2026-03-07T09:20:00+08:00",
    reportGeneratedAt: null,
  },
  {
    id: "p-1003",
    name: "实验室资产追踪系统",
    description: "资产流转、报修流程与审计追溯",
    status: "done",
    version: "V2.0",
    fp: 98,
    estimatedCost: 168000,
    lastUpdated: "2026-03-04T15:00:00+08:00",
    reportGeneratedAt: "2026-03-04T15:00:00+08:00",
  },
]

function getStatusMeta(status: ProjectStatus) {
  const map: Record<ProjectStatus, { label: string; className: string }> = {
    draft: { label: "草稿", className: "bg-slate-100 text-slate-800 hover:bg-slate-100" },
    parsing: { label: "解析中", className: "bg-amber-100 text-amber-800 hover:bg-amber-100" },
    analyzing: { label: "分析中", className: "bg-sky-100 text-sky-800 hover:bg-sky-100" },
    pending_confirm: { label: "待确认", className: "bg-orange-100 text-orange-800 hover:bg-orange-100" },
    completed: { label: "已完成", className: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100" },
    archived: { label: "已归档", className: "bg-zinc-100 text-zinc-800 hover:bg-zinc-100" },
    done: { label: "已完成", className: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100" },
  }

  return map[status]
}

function formatCost(value: number | null) {
  if (value === null) return "--"

  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(value)
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

function mapApiProjectToItem(project: ApiProject): ProjectItem {
  return {
    id: project.id,
    name: project.name,
    description: project.description || "--",
    status: project.status,
    version: project.current_version_id ? project.current_version_id.slice(0, 8) : "--",
    fp: null,
    estimatedCost: null,
    lastUpdated: project.updated_at,
    reportGeneratedAt: null,
  }
}

async function fetchProjectsFromBackend(signal: AbortSignal): Promise<ProjectItem[]> {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
  const res = await fetch(`${apiBase}/projects`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" },
  })

  if (!res.ok) {
    throw new Error(`鑾峰彇椤圭洰鍒楄〃澶辫触锛?{res.status}`)
  }

  const data = (await res.json()) as ApiProject[]
  return Array.isArray(data) ? data.map(mapApiProjectToItem) : []
}

async function createProjectOnBackend(payload: { name: string; description: string }): Promise<ProjectItem> {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
  const res = await fetch(`${apiBase}/projects`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!res.ok) {
    throw new Error(`鍒涘缓椤圭洰澶辫触锛?{res.status}`)
  }

  const data = (await res.json()) as ApiProject
  return mapApiProjectToItem(data)
}

async function deleteProjectOnBackend(projectId: string): Promise<void> {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
  const res = await fetch(`${apiBase}/projects/${projectId}`, {
    method: "DELETE",
  })

  if (!res.ok) {
    throw new Error(`鍒犻櫎椤圭洰澶辫触锛?{res.status}`)
  }
}

export default function HomePage() {
  const [projects, setProjects] = useState<ProjectItem[]>([])
  const [query, setQuery] = useState("")
  const [usingMockData, setUsingMockData] = useState(false)
  const [projectToDelete, setProjectToDelete] = useState<ProjectItem | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState("")
  const [deleteAlert, setDeleteAlert] = useState<DeleteAlert | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    fetchProjectsFromBackend(controller.signal)
      .then((items) => {
        setProjects(items)
        setUsingMockData(false)
      })
      .catch(() => {
        setProjects(MOCK_PROJECTS)
        setUsingMockData(true)
      })

    return () => controller.abort()
  }, [])

  const filteredProjects = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return projects

    return projects.filter(
      (item) => item.name.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword)
    )
  }, [projects, query])

  const stats = useMemo(() => {
    const total = filteredProjects.length
    const pending = filteredProjects.filter((item) => item.status === "pending_confirm").length
    const reports = filteredProjects.filter((item) => item.reportGeneratedAt).length
    return { total, pending, reports }
  }, [filteredProjects])

  useEffect(() => {
    if (!deleteAlert) return

    const timer = window.setTimeout(() => {
      setDeleteAlert(null)
    }, 3000)

    return () => window.clearTimeout(timer)
  }, [deleteAlert])

  const hasProjects = projects.length > 0

  async function handleCreateProject({ name, description }: CreateProjectPayload) {
    const created = await createProjectOnBackend({ name, description })
    setProjects((prev) => [created, ...prev])
    setUsingMockData(false)
  }

  async function handleConfirmDelete() {
    const targetProject = projectToDelete
    if (!targetProject) return

    setIsDeleting(true)
    setDeleteError("")

    try {
      if (!usingMockData) {
        await deleteProjectOnBackend(targetProject.id)
      }

      setProjects((prev) => prev.filter((item) => item.id !== targetProject.id))
      setProjectToDelete(null)
      setDeleteAlert({
        variant: "default",
        title: "删除成功",
        description: `项目「${targetProject.name}」已删除。`,
      })
    } catch (deleteProjectError) {
      const errorMessage = deleteProjectError instanceof Error ? deleteProjectError.message : "删除项目失败。"
      setDeleteError(errorMessage)
      setDeleteAlert({
        variant: "destructive",
        title: "删除失败",
        description: errorMessage,
      })
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      {deleteAlert ? (
        <div className="fixed right-6 top-6 z-[60] w-[min(92vw,420px)]">
          <Alert variant={deleteAlert.variant} className="shadow-lg">
            <AlertTitle>{deleteAlert.title}</AlertTitle>
            <AlertDescription>{deleteAlert.description}</AlertDescription>
            <AlertAction>
              <Button size="xs" variant="ghost" onClick={() => setDeleteAlert(null)}>
                关闭
              </Button>
            </AlertAction>
          </Alert>
        </div>
      ) : null}

      <div className="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="text-sm font-extrabold tracking-[0.1em] text-slate-700">SOFTPLAN</div>
          <div className="relative w-full sm:flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <Input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="按项目名称或描述搜索"
              className="pl-9"
              aria-label="全局搜索"
            />
          </div>
          {hasProjects ? <CreateProjectDialog onCreateProject={handleCreateProject} /> : null}
        </div>
      </div>

      <div className="mt-5 space-y-4">
        {usingMockData ? (
          <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            后端暂不可用，当前展示本地示例数据。
          </div>
        ) : null}

        <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="项目概览">
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-slate-500">项目总数</p>
              <p className="mt-1 text-3xl font-semibold">{stats.total}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-slate-500">待确认项目</p>
              <p className="mt-1 text-3xl font-semibold">{stats.pending}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-slate-500">已生成报告</p>
              <p className="mt-1 text-3xl font-semibold">{stats.reports}</p>
            </CardContent>
          </Card>
        </section>
        {hasProjects ? (
          <Card className="overflow-hidden">
            <Table className="min-w-[980px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>项目名称</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>当前版本</TableHead>
                  <TableHead>规模 (FP)</TableHead>
                  <TableHead>预估成本</TableHead>
                  <TableHead>最后更新</TableHead>
                  <TableHead>快捷操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredProjects.map((item) => {
                  const status = getStatusMeta(item.status)

                  return (
                    <TableRow key={item.id}>
                      <TableCell>
                        <div className="space-y-1">
                          <Link href={`/projects/${item.id}`} className="font-semibold text-slate-900 hover:underline">
                            {item.name}
                          </Link>
                          <p className="text-xs text-slate-500">{item.description}</p>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge className={status.className}>{status.label}</Badge>
                      </TableCell>
                      <TableCell>{item.version}</TableCell>
                      <TableCell>{item.fp ?? "--"}</TableCell>
                      <TableCell>{formatCost(item.estimatedCost)}</TableCell>
                      <TableCell>{formatDate(item.lastUpdated)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" variant="outline">
                            查看报告
                          </Button>
                          <Button size="sm" variant="secondary">
                            开始分析
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => {
                              setDeleteError("")
                              setProjectToDelete(item)
                            }}
                          >
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
                {filteredProjects.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center text-slate-500">
                      未找到匹配的项目
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-6">
              <Empty className="rounded-2xl border border-dashed border-slate-300 bg-slate-50">
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <FolderOpen className="size-4" />
                  </EmptyMedia>
                  <EmptyTitle>暂无项目</EmptyTitle>
                  <EmptyDescription>当前还没有项目，请先创建一个新项目。</EmptyDescription>
                </EmptyHeader>
                <EmptyContent>
                  <CreateProjectDialog onCreateProject={handleCreateProject} />
                </EmptyContent>
              </Empty>
            </CardContent>
          </Card>
        )}
      </div>

      <AlertDialog
        open={Boolean(projectToDelete)}
        onOpenChange={(open) => {
          if (!open && !isDeleting) {
            setProjectToDelete(null)
            setDeleteError("")
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除项目</AlertDialogTitle>
            <AlertDialogDescription>
              {projectToDelete ? `确定要删除「${projectToDelete.name}」吗？该操作不可恢复。` : "确定要删除该项目吗？"}
            </AlertDialogDescription>
            {deleteError ? <AlertDialogDescription className="text-destructive">{deleteError}</AlertDialogDescription> : null}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={isDeleting}
              onClick={(event) => {
                event.preventDefault()
                void handleConfirmDelete()
              }}
            >
              {isDeleting ? "删除中..." : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
