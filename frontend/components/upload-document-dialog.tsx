"use client"

import { ChangeEvent, DragEvent, FormEvent, useRef, useState } from "react"
import { Upload } from "lucide-react"

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
import { cn } from "@/lib/utils"

export type UploadDocumentPayload = {
  file: File
  name: string
  description: string
}

type UploadDocumentDialogProps = {
  onUploadDocument: (payload: UploadDocumentPayload) => Promise<void>
  disabled?: boolean
  isUploading?: boolean
}

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function UploadDocumentDialog({
  onUploadDocument,
  disabled = false,
  isUploading = false,
}: UploadDocumentDialogProps) {
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [error, setError] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const busy = isUploading || isSubmitting

  function resetForm() {
    setFile(null)
    setName("")
    setDescription("")
    setError("")
    setIsDragActive(false)
  }

  function applySelectedFile(nextFile: File) {
    setFile(nextFile)
    setName(nextFile.name)
    setError("")
  }

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const selectedFile = event.target.files?.[0]
    event.target.value = ""
    if (!selectedFile) return
    applySelectedFile(selectedFile)
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragActive(true)
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragActive(false)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragActive(false)
    const droppedFile = event.dataTransfer.files?.[0]
    if (!droppedFile) return
    applySelectedFile(droppedFile)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!file) {
      setError("请先选择要上传的文档。")
      return
    }

    const normalizedName = name.trim()
    if (!normalizedName) {
      setError("文档名称不能为空。")
      return
    }

    setError("")
    setIsSubmitting(true)
    try {
      await onUploadDocument({
        file,
        name: normalizedName,
        description: description.trim(),
      })
      setOpen(false)
      resetForm()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "文档上传失败，请重试。")
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
        <Button disabled={disabled || busy}>上传文档</Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={handleSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>上传文档</DialogTitle>
            <DialogDescription>支持拖拽或选择文件，确认名称与描述后再提交上传。</DialogDescription>
          </DialogHeader>

          <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileInputChange} disabled={busy} />

          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={cn(
              "rounded-xl border border-dashed p-4 text-center transition-colors",
              isDragActive ? "border-sky-400 bg-sky-50" : "border-slate-300 bg-slate-50"
            )}
          >
            <div className="mx-auto mb-2 flex size-9 items-center justify-center rounded-lg bg-white text-slate-600 ring-1 ring-slate-200">
              <Upload className="size-4" />
            </div>
            <p className="text-sm text-slate-700">{file ? `已选择：${file.name}` : "拖拽文件到这里，或点击选择文件"}</p>
            <p className="mt-1 text-xs text-slate-500">{file ? `大小：${formatFileSize(file.size)}` : "单次仅上传一个文件"}</p>
            <Button
              type="button"
              variant="outline"
              className="mt-3"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
            >
              选择文件
            </Button>
          </div>

          <div className="grid gap-2">
            <label htmlFor="upload-document-name" className="text-sm font-medium text-slate-700">
              文档名称
            </label>
            <Input
              id="upload-document-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="请输入文档名称"
              disabled={busy}
              maxLength={255}
              required
            />
          </div>

          <div className="grid gap-2">
            <label htmlFor="upload-document-description" className="text-sm font-medium text-slate-700">
              文档描述
            </label>
            <Textarea
              id="upload-document-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="可选，补充文档用途或背景"
              disabled={busy}
            />
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline" disabled={busy}>
                取消
              </Button>
            </DialogClose>
            <Button type="submit" disabled={busy || !file}>
              {busy ? "上传中..." : "确认上传"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

