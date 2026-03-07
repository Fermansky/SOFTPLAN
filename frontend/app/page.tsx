"use client";

import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";

import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";

type ProjectStatus = "parsing" | "analyzing" | "pending_confirm" | "done";

type ProjectItem = {
  id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  version: string;
  fp: number | null;
  estimatedCost: number | null;
  lastUpdated: string;
  reportGeneratedAt?: string | null;
};

const MOCK_PROJECTS: ProjectItem[] = [
  {
    id: "p-1001",
    name: "智慧教务平台升级",
    description: "教务排课与教师工作量核算模块整合",
    status: "pending_confirm",
    version: "V1.2",
    fp: 126,
    estimatedCost: 286000,
    lastUpdated: "2026-03-06T10:30:00+08:00",
    reportGeneratedAt: "2026-03-05T17:10:00+08:00"
  },
  {
    id: "p-1002",
    name: "科研数据采集中台",
    description: "多源数据接入、清洗、统一检索",
    status: "analyzing",
    version: "V0.9",
    fp: 214,
    estimatedCost: null,
    lastUpdated: "2026-03-07T09:20:00+08:00",
    reportGeneratedAt: null
  },
  {
    id: "p-1003",
    name: "实验室资产追踪系统",
    description: "资产流转、报修、可追溯审计",
    status: "done",
    version: "V2.0",
    fp: 98,
    estimatedCost: 168000,
    lastUpdated: "2026-03-04T15:00:00+08:00",
    reportGeneratedAt: "2026-03-04T15:00:00+08:00"
  },
  {
    id: "p-1004",
    name: "招生咨询智能问答",
    description: "招生政策知识库与多轮问答支持",
    status: "parsing",
    version: "V0.3",
    fp: null,
    estimatedCost: null,
    lastUpdated: "2026-03-07T11:05:00+08:00",
    reportGeneratedAt: null
  }
];

function getStatusMeta(status: ProjectStatus) {
  const map: Record<ProjectStatus, { label: string; className: string }> = {
    parsing: { label: "解析中", className: "bg-amber-100 text-amber-800 hover:bg-amber-100" },
    analyzing: { label: "分析中", className: "bg-sky-100 text-sky-800 hover:bg-sky-100" },
    pending_confirm: { label: "待确认", className: "bg-orange-100 text-orange-800 hover:bg-orange-100" },
    done: { label: "已完成", className: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100" }
  };
  return map[status];
}

function formatCost(value: number | null) {
  if (value === null) return "--";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0
  }).format(value);
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  });
}

async function fetchProjectsFromBackend(signal: AbortSignal): Promise<ProjectItem[]> {
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const res = await fetch(`${apiBase}/projects`, {
    method: "GET",
    signal,
    headers: { Accept: "application/json" }
  });

  if (!res.ok) {
    throw new Error(`Fetch projects failed: ${res.status}`);
  }

  const data = (await res.json()) as ProjectItem[];
  return Array.isArray(data) ? data : [];
}

export default function HomePage() {
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [query, setQuery] = useState("");
  const [usingMockData, setUsingMockData] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    fetchProjectsFromBackend(controller.signal)
      .then((items) => {
        setProjects(items.length > 0 ? items : MOCK_PROJECTS);
        setUsingMockData(items.length === 0);
      })
      .catch(() => {
        setProjects(MOCK_PROJECTS);
        setUsingMockData(true);
      });

    return () => controller.abort();
  }, []);

  const filteredProjects = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return projects;
    return projects.filter(
      (item) => item.name.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword)
    );
  }, [projects, query]);

  const stats = useMemo(() => {
    const total = filteredProjects.length;
    const pending = filteredProjects.filter((item) => item.status === "pending_confirm").length;
    const reports = filteredProjects.filter((item) => item.reportGeneratedAt).length;
    return { total, pending, reports };
  }, [filteredProjects]);

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="rounded-2xl border border-slate-200 bg-white/80 p-4 shadow-sm backdrop-blur sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="text-sm font-extrabold tracking-[0.1em] text-slate-700">SOFTPLAN</div>
          <div className="relative w-full sm:flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <Input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索项目名称或描述"
              className="pl-9"
              aria-label="全局搜索"
            />
          </div>
          <Button>+ 新建估算项目</Button>
        </div>
      </div>

      <div className="mt-5 space-y-4">
        {usingMockData ? (
          <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            当前未连接后端，已展示本地示例数据。
          </div>
        ) : null}

        <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="项目概览">
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-slate-500">总项目数</p>
              <p className="mt-1 text-3xl font-semibold">{stats.total}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-slate-500">待确认项</p>
              <p className="mt-1 text-3xl font-semibold">{stats.pending}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-5">
              <p className="text-sm text-slate-500">最近生成的报告数</p>
              <p className="mt-1 text-3xl font-semibold">{stats.reports}</p>
            </CardContent>
          </Card>
        </section>

        <Card className="overflow-hidden">
          <Table className="min-w-[980px]">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>项目名称</TableHead>
                <TableHead>分析状态</TableHead>
                <TableHead>当前版本</TableHead>
                <TableHead>规模 (FP)</TableHead>
                <TableHead>预估成本</TableHead>
                <TableHead>最后更新</TableHead>
                <TableHead>快捷操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredProjects.map((item) => {
                const status = getStatusMeta(item.status);
                return (
                  <TableRow key={item.id}>
                    <TableCell>
                      <div className="space-y-1">
                        <a href="#" className="font-semibold text-slate-900 hover:underline">
                          {item.name}
                        </a>
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
                          进入分析
                        </Button>
                        <Button size="sm" variant="destructive">
                          删除
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
              {filteredProjects.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-center text-slate-500">
                    未找到匹配项目
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </Card>
      </div>
    </div>
  );
}

