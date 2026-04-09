"use client"

import type { ReactNode } from "react"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Bot, ChevronRight, Settings2 } from "lucide-react"

import { DetailPageHeader } from "@/components/detail-page-header"
import { Button } from "@/components/ui/button"
import { PAGE_CONTAINER_CLASS } from "@/lib/layout"
import { cn } from "@/lib/utils"

const SETTINGS_NAV_ITEMS = [
  {
    href: "/settings/model",
    label: "模型",
    description: "管理模型相关配置与服务接入的页面骨架。",
    icon: Bot,
  },
]

function getCurrentSettingsItem(pathname: string) {
  return SETTINGS_NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))
}

export function SettingsShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const currentItem = getCurrentSettingsItem(pathname) ?? SETTINGS_NAV_ITEMS[0]

  return (
    <div className={PAGE_CONTAINER_CLASS}>
      <DetailPageHeader
        items={[
          { label: "首页", href: "/" },
          { label: "设置" },
          { label: currentItem.label },
        ]}
        title={currentItem.label}
        description={currentItem.description}
        actions={
          <Button asChild variant="outline">
            <Link href="/">返回首页</Link>
          </Button>
        }
      />

      <section className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm xl:h-[calc(100vh-14rem)] xl:min-h-0">
        <div className="grid h-full min-h-0 gap-0 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="min-h-0 border-b border-slate-200 bg-slate-50/80 xl:overflow-y-auto xl:border-r xl:border-b-0">
            <div className="border-b border-slate-200 px-5 py-5">
              <div className="flex items-center gap-3">
                <div className="flex size-11 items-center justify-center rounded-2xl bg-slate-900 text-white shadow-sm">
                  <Settings2 className="size-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-900">设置目录</p>
                  <p className="text-sm text-slate-500">在正文范围内切换当前配置模块。</p>
                </div>
              </div>
            </div>

            <nav className="p-3" aria-label="设置菜单">
              <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                可用配置
              </p>
              <ul className="space-y-1.5">
                {SETTINGS_NAV_ITEMS.map((item) => {
                  const Icon = item.icon
                  const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`)

                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        className={cn(
                          "group flex items-start gap-3 rounded-2xl border px-3 py-3 text-left transition-colors",
                          isActive
                            ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                            : "border-transparent bg-white text-slate-700 hover:border-slate-200 hover:bg-slate-100"
                        )}
                      >
                        <div
                          className={cn(
                            "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-xl",
                            isActive ? "bg-white/15 text-white" : "bg-slate-100 text-slate-700"
                          )}
                        >
                          <Icon className="size-4" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-3">
                            <span className="font-medium">{item.label}</span>
                            <ChevronRight
                              className={cn(
                                "size-4 shrink-0 transition-transform group-hover:translate-x-0.5",
                                isActive ? "text-white/80" : "text-slate-400"
                              )}
                            />
                          </div>
                          <p
                            className={cn(
                              "mt-1 line-clamp-2 text-xs leading-5",
                              isActive ? "text-white/75" : "text-slate-500"
                            )}
                          >
                            {item.description}
                          </p>
                        </div>
                      </Link>
                    </li>
                  )
                })}
              </ul>
            </nav>
          </aside>

          <div className="flex min-h-0 min-w-0 flex-col bg-white">
            <div className="shrink-0 border-b border-slate-200 px-5 py-4 lg:px-6">
              <p className="text-sm font-semibold text-slate-900">{currentItem.label}</p>
              <p className="mt-1 text-sm text-slate-500">
                右侧区域承载当前设置项的详细内容，后续可以在这里继续补充完整的配置表单与状态信息。
              </p>
            </div>

            <div className="min-h-0 flex-1 px-5 py-5 xl:overflow-hidden lg:px-6 lg:py-6">{children}</div>
          </div>
        </div>
      </section>
    </div>
  )
}


