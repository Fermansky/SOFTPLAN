import Link from "next/link"

import { DetailPageHeader } from "@/components/detail-page-header"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { PAGE_CONTAINER_CLASS } from "@/lib/layout"

const SETTING_SECTIONS = [
  {
    title: "系统设置",
    description: "这里将用于放置基础运行参数、环境信息与系统级开关。",
    status: "即将支持",
  },
  {
    title: "模型与服务",
    description: "这里将用于管理模型提供方、服务连通性以及相关策略配置。",
    status: "暂未开放",
  },
  {
    title: "界面与偏好",
    description: "这里将用于保存个人偏好、默认行为与界面展示选项。",
    status: "规划中",
  },
]

export default function SettingsPage() {
  return (
    <div className={PAGE_CONTAINER_CLASS}>
      <DetailPageHeader
        items={[
          { label: "首页", href: "/" },
          { label: "设置" },
        ]}
        title="设置"
        description="设置界面已预留，后续将在这里逐步补充系统与偏好配置能力。"
        actions={
          <Button asChild variant="outline">
            <Link href="/">返回首页</Link>
          </Button>
        }
      />

      <div className="space-y-4">
        <Card className="border-dashed border-slate-300 bg-slate-50/80">
          <CardContent className="p-5 text-sm text-slate-600">
            当前页面仅完成入口与页面框架，具体设置项暂未接入，也不会触发任何保存或后端请求。
          </CardContent>
        </Card>

        <section className="grid grid-cols-1 gap-4 lg:grid-cols-3" aria-label="设置模块占位">
          {SETTING_SECTIONS.map((section) => (
            <Card key={section.title}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <CardTitle>{section.title}</CardTitle>
                  <Badge className="bg-slate-100 text-slate-700 hover:bg-slate-100">{section.status}</Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3 pt-0 text-sm text-slate-600">
                <p>{section.description}</p>
                <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-2 text-xs text-slate-500">
                  预留模块区域，后续会在这里补充具体设置表单与说明。
                </div>
              </CardContent>
            </Card>
          ))}
        </section>
      </div>
    </div>
  )
}
