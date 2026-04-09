import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

const MODEL_SETTING_BLOCKS = [
  {
    title: "模型提供方",
    description: "后续会在这里管理可接入的模型服务商、访问方式与基础参数。",
    status: "即将支持",
    placeholder: "预留模型服务提供方列表、接入凭证和默认请求策略。",
  },
  {
    title: "默认模型选择",
    description: "后续会在这里配置不同任务场景下的默认模型与优先级。",
    status: "暂未开放",
    placeholder: "预留默认模型、备用模型和场景映射等配置能力。",
  },
  {
    title: "服务连通性",
    description: "后续会在这里展示模型服务健康状态、测试结果与调用准备情况。",
    status: "规划中",
    placeholder: "预留服务检测、调用测试和失败诊断等状态面板。",
  },
]

export default function ModelSettingsPage() {
  return (
    <div className="space-y-4">
      <Card className="border-dashed border-slate-300 bg-slate-50/80">
        <CardHeader>
          <CardTitle>模型设置面板</CardTitle>
          <CardDescription>
            当前先完成设置区的正文双栏结构与“模型”模块占位，后续再逐步接入真实表单、保存逻辑和服务请求。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-slate-600">
          <p>
            左侧目录负责在设置项之间切换，右侧当前区域会逐步演变成独立的配置小页面。现在先保留模块分组和状态占位，方便后续继续填充。
          </p>
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white/80 px-4 py-3 text-xs leading-6 text-slate-500">
            当前不包含真实配置读取、表单校验、保存交互或后端 API 请求。
          </div>
        </CardContent>
      </Card>

      <div className="space-y-4" aria-label="模型设置占位模块">
        {MODEL_SETTING_BLOCKS.map((block) => (
          <Card key={block.title}>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <CardTitle>{block.title}</CardTitle>
                  <CardDescription>{block.description}</CardDescription>
                </div>
                <Badge className="bg-slate-100 text-slate-700 hover:bg-slate-100">{block.status}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-sm text-slate-600">
              <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-3">
                {block.placeholder}
              </div>
              <div className="grid gap-3 text-xs text-slate-500 sm:grid-cols-2">
                <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">预留配置字段与说明区域</div>
                <div className="rounded-xl border border-slate-200 bg-white px-3 py-3">预留状态反馈与辅助提示区域</div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
