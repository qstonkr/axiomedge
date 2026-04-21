"use client";

import { useRef, useState, type DragEvent } from "react";

import { Button, useToast, cn } from "@/components/ui";
import {
  useChunkedUpload,
  type ChunkedUploadProgress,
} from "@/hooks/useChunkedUpload";
import { useUploadDocument } from "@/hooks/useMyKnowledge";

// Backend ingestion_gate IG-07 의 max_file_size_mb 와 sync 유지 — 5GB.
// 변경 시 src/config/weights/pipeline.py 도 함께.
const MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024 * 1024;
const MAX_FILE_SIZE_LABEL = "5 GB";

// Bulk upload (presigned URL flow) 으로 분기되는 임계값.
// 작은 케이스 (1~4 파일, 누적 100MB 미만) 는 기존 sequential multipart 가
// 1 round-trip 이라 더 빠름. 대량 케이스만 chunked flow 진입.
const BULK_FILE_COUNT_THRESHOLD = 5;
const BULK_TOTAL_SIZE_THRESHOLD = 100 * 1024 * 1024;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function DocumentUploader({ kbId }: { kbId: string }) {
  const toast = useToast();
  const upload = useUploadDocument(kbId);
  const chunked = useChunkedUpload();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<ChunkedUploadProgress | null>(
    null,
  );

  async function uploadSequential(files: File[]) {
    for (const file of files) {
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

  async function uploadBulk(files: File[]) {
    setBulkProgress({
      uploaded: 0, totalBytes: files.reduce((s, f) => s + f.size, 0),
      uploadedFiles: 0, totalFiles: files.length,
      processed: 0, failed: 0, status: "uploading", errors: [],
    });
    try {
      const result = await chunked.upload(kbId, files, setBulkProgress);
      const { processed, failed } = result.finalStatus;
      if (failed === 0) {
        toast.push(`${processed}건 업로드 완료`, "success");
      } else {
        toast.push(
          `${processed}건 성공, ${failed}건 실패 — 상태 패널에서 확인`,
          "warning",
        );
      }
    } catch (err) {
      const detail =
        err instanceof Error ? err.message : "대량 업로드 실패";
      toast.push(detail, "danger");
    } finally {
      // progress UI 는 5초 더 보여주고 사라짐 (사용자 결과 확인 시간).
      setTimeout(() => setBulkProgress(null), 5000);
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    const list = Array.from(files);
    if (list.length === 0) return;
    // Client-side size check — 5GB 초과 파일은 server 까지 안 보냄.
    const oversized = list.filter((f) => f.size > MAX_FILE_SIZE_BYTES);
    const valid = list.filter((f) => f.size <= MAX_FILE_SIZE_BYTES);
    for (const f of oversized) {
      toast.push(
        `${f.name} (${formatBytes(f.size)}) — 파일당 최대 ${MAX_FILE_SIZE_LABEL} 까지 가능`,
        "warning",
      );
    }
    if (valid.length === 0) return;

    // 분기 — 작은 케이스는 기존 multipart 가 더 빠름.
    const totalSize = valid.reduce((s, f) => s + f.size, 0);
    const useBulk =
      valid.length >= BULK_FILE_COUNT_THRESHOLD
      || totalSize >= BULK_TOTAL_SIZE_THRESHOLD;
    if (useBulk) {
      await uploadBulk(valid);
    } else {
      await uploadSequential(valid);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      void uploadFiles(e.dataTransfer.files);
    }
  }

  const isBusy = upload.isPending || chunked.isPending;

  return (
    <div className="space-y-3">
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
            <br />
            대량 업로드 ({BULK_FILE_COUNT_THRESHOLD}개 이상 또는 누적{" "}
            {formatBytes(BULK_TOTAL_SIZE_THRESHOLD)} 이상) 는 자동으로 직접
            업로드 모드 (병렬 5건 + 진행률) 사용.
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
          disabled={isBusy}
          onClick={() => inputRef.current?.click()}
        >
          {isBusy ? "업로드 중…" : "파일 선택"}
        </Button>
      </div>

      {bulkProgress && <BulkUploadProgressPanel progress={bulkProgress} />}
    </div>
  );
}

function BulkUploadProgressPanel({
  progress,
}: {
  progress: ChunkedUploadProgress;
}) {
  const uploadPct =
    progress.totalBytes > 0
      ? Math.round((progress.uploaded / progress.totalBytes) * 100)
      : 0;
  const ingestPct =
    progress.totalFiles > 0
      ? Math.round(
          ((progress.processed + progress.failed) / progress.totalFiles) * 100,
        )
      : 0;

  const phaseLabel: Record<ChunkedUploadProgress["status"], string> = {
    uploading: "1/2 — 파일 업로드 중",
    finalizing: "1.5/2 — 서버 처리 시작",
    processing: "2/2 — 서버 ingest 중 (분석/벡터화)",
    completed: "✅ 완료",
    failed: "❌ 일부 실패",
  };

  return (
    <div className="space-y-3 rounded-md border border-border-default bg-bg-default p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-medium text-fg-default">
          📥 대량 업로드: {phaseLabel[progress.status]}
        </span>
        <span className="text-fg-subtle">
          {progress.uploadedFiles}/{progress.totalFiles} files
        </span>
      </div>

      <div>
        <div className="mb-1 flex justify-between text-[10px] text-fg-subtle">
          <span>1) 브라우저 → 스토리지 PUT</span>
          <span>{uploadPct}%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-bg-emphasis">
          <div
            className="h-full bg-accent-default transition-all"
            style={{ width: `${uploadPct}%` }}
          />
        </div>
      </div>

      <div>
        <div className="mb-1 flex justify-between text-[10px] text-fg-subtle">
          <span>
            2) 서버 ingest ({progress.processed} 처리 / {progress.failed} 실패)
          </span>
          <span>{ingestPct}%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-bg-emphasis">
          <div
            className={cn(
              "h-full transition-all",
              progress.failed > 0
                ? "bg-warning-default"
                : "bg-success-default",
            )}
            style={{ width: `${ingestPct}%` }}
          />
        </div>
      </div>

      {progress.errors.length > 0 && (
        <details className="text-[10px] text-fg-muted">
          <summary className="cursor-pointer text-danger-default">
            실패 {progress.errors.length}건 보기
          </summary>
          <ul className="mt-1 space-y-1">
            {progress.errors.slice(0, 10).map((e, i) => (
              <li key={`${e.filename}-${i}`}>
                <span className="font-mono">{e.filename}</span>: {e.error_message}
              </li>
            ))}
            {progress.errors.length > 10 && (
              <li>… 외 {progress.errors.length - 10}건</li>
            )}
          </ul>
        </details>
      )}
    </div>
  );
}
