"use client"

import { FormEvent, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"

export type CreateProjectPayload = {
  name: string
  description: string
}

type CreateProjectDialogProps = {
  onCreateProject: (payload: CreateProjectPayload) => Promise<void>
}

export function CreateProjectDialog({ onCreateProject }: CreateProjectDialogProps) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [error, setError] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)

  function resetForm() {
    setName("")
    setDescription("")
    setError("")
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalizedName = name.trim()

    if (!normalizedName) {
      setError("项目名称不能为空。")
      return
    }

    setIsSubmitting(true)
    setError("")

    try {
      await onCreateProject({
        name: normalizedName,
        description: description.trim(),
      })
      setOpen(false)
      resetForm()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "创建项目失败。")
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        if (!nextOpen) {
          resetForm()
        }
      }}
    >
      <DialogTrigger asChild>
        <Button onClick={resetForm}>+ 新建项目</Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-sm">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>新建项目</DialogTitle>
            <DialogDescription>填写项目名称和描述后，提交到后端创建项目。</DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <label htmlFor="project-name" className="text-sm font-medium">
              项目名称
            </label>
            <Input
              id="project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：Softplan MVP"
              maxLength={255}
              disabled={isSubmitting}
              required
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="project-description" className="text-sm font-medium">
              项目描述
            </label>
            <Textarea
              id="project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="可选，填写项目背景或目标"
              disabled={isSubmitting}
            />
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline" disabled={isSubmitting}>
                取消
              </Button>
            </DialogClose>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "创建中..." : "创建"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
