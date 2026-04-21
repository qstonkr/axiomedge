"use client";

import { useRef, useState, type DragEvent } from "react";

import { Button, useToast, cn } from "@/components/ui";
import { useUploadDocument } from "@/hooks/useMyKnowledge";

// Backend ingestion_gate IG-07 의 max_file_size_mb 와 sync 유지 — 5GB.
// 변경 시 src/config/weights/pipeline.py 도 함께.
const MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024 * 1024;
const MAX_FILE_SIZE_LABEL = "5 GB";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function DocumentUploader({ kbId }: { kbId: string }) {
  const toast = useToast();
  const upload = useUploadDocument(kbId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    // Client-side size check — 5GB 초과 파일은 server 까지 안 보냄. server
    // 의 ingestion_gate IG-07 가 어차피 거부하지만 wasted upload 차단.
    const oversized = list.filter((f) => f.size > MAX_FILE_SIZE_BYTES);
    const valid = list.filter((f) => f.size <= MAX_FILE_SIZE_BYTES);
    for (const f of oversized) {
      toast.push(
        `${f.name} (${formatBytes(f.size)}) — 파일당 최대 ${MAX_FILE_SIZE_LABEL} 까지 가능`,
        "warning",
      );
    }
    if (valid.length === 0) return;
    // Sequential — keeps the FastAPI ingestion pipeline from getting hammered
    // and lets us surface per-file errors via toast.
    for (const file of valid) {
      try {
        await upload.mutateAsync(file);
        toast.push(`${file.name} 업로드 완료`, "success");
      } catch (err) {
        const detail =
          err instanceof Error ? err.message : `${file.name} 업로드 실패`;
        toast.push(detail, "danger");
      }
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      void uploadFiles(e.dataTransfer.files);
    }
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed bg-bg-subtle px-6 py-10 text-center transition-colors",
        dragOver
          ? "border-accent-default bg-accent-subtle"
          : "border-border-default",
      )}
    >
      <p className="text-sm text-fg-muted">
        파일을 드래그하거나 클릭하여 선택하세요.
        <br />
        <span className="text-xs text-fg-subtle">
          PDF / Markdown / Word / Excel / 텍스트 지원 · 파일당 최대 {MAX_FILE_SIZE_LABEL}
        </span>
      </p>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files) void uploadFiles(e.target.files);
          if (inputRef.current) inputRef.current.value = "";
        }}
      />
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={upload.isPending}
        onClick={() => inputRef.current?.click()}
      >
        {upload.isPending ? "업로드 중…" : "파일 선택"}
      </Button>
    </div>
  );
}
