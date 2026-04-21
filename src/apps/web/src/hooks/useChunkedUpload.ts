"use client";

import { useCallback, useRef, useState } from "react";

import {
  finalizeBulkUpload,
  getBulkUploadStatus,
  initBulkUpload,
  type BulkUploadInitEntry,
} from "@/lib/api/endpoints";

const DEFAULT_CONCURRENCY = 5;
const STATUS_POLL_INTERVAL_MS = 5000;

export type ChunkedUploadProgress = {
  // 브라우저 → S3 업로드 단계
  uploaded: number;       // PUT 완료 byte 합
  totalBytes: number;     // 전체 byte
  uploadedFiles: number;  // PUT 완료 파일 수
  totalFiles: number;
  // 서버 ingest 단계 (finalize 후)
  processed: number;
  failed: number;
  status: "uploading" | "finalizing" | "processing" | "completed" | "failed";
  errors: { filename: string; error_message: string }[];
};

export type ChunkedUploadResult = {
  sessionId: string;
  finalStatus: ChunkedUploadProgress;
};

/**
 * 대량 파일 업로드 — presigned URL flow.
 *
 * 1. POST /uploads/init → presigned PUT URL × N
 * 2. concurrency throttle (default 5) 로 각 파일 XHR PUT
 *    - xhr.upload.onprogress → onProgress
 *    - 실패 시 1회 retry (presigned URL 재사용, 1h 유효)
 * 3. POST /uploads/{sid}/finalize { failed_indices } → arq enqueue
 * 4. polling /uploads/{sid}/status (5s) → onProgress
 *
 * 백엔드 byte path 안 거침 — API 프로세스 RAM 부담 0.
 */
export function useChunkedUpload(opts?: {
  concurrency?: number;
  pollIntervalMs?: number;
}) {
  const concurrency = opts?.concurrency ?? DEFAULT_CONCURRENCY;
  const pollIntervalMs = opts?.pollIntervalMs ?? STATUS_POLL_INTERVAL_MS;

  const [isPending, setPending] = useState(false);
  const cancelRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    cancelRef.current?.abort();
  }, []);

  const upload = useCallback(
    async (
      kbId: string,
      files: File[],
      onProgress?: (p: ChunkedUploadProgress) => void,
    ): Promise<ChunkedUploadResult> => {
      if (files.length === 0) {
        throw new Error("upload: empty file list");
      }

      const totalBytes = files.reduce((s, f) => s + f.size, 0);
      const ctrl = new AbortController();
      cancelRef.current = ctrl;
      setPending(true);

      const progress: ChunkedUploadProgress = {
        uploaded: 0,
        totalBytes,
        uploadedFiles: 0,
        totalFiles: files.length,
        processed: 0,
        failed: 0,
        status: "uploading",
        errors: [],
      };
      const emit = () => onProgress?.({ ...progress });

      try {
        // 1) init — presigned URL 발급
        const init = await initBulkUpload(
          kbId,
          files.map((f) => ({ name: f.name, size: f.size })),
        );
        const sessionId = init.session_id;
        const byIdx = new Map<number, BulkUploadInitEntry>();
        for (const u of init.uploads) byIdx.set(u.file_idx, u);

        // 2) concurrency-throttled PUT
        const failedIndices: number[] = [];
        // per-file 누적 byte (XHR progress 가 cumulative 라 마지막 값만 더하면 됨)
        const perFileLoaded = new Map<number, number>();

        async function putOne(idx: number, file: File): Promise<void> {
          const entry = byIdx.get(idx);
          if (!entry) {
            failedIndices.push(idx);
            return;
          }
          const attempt = async () => {
            await new Promise<void>((resolve, reject) => {
              const xhr = new XMLHttpRequest();
              xhr.open("PUT", entry.presigned_url, true);
              if (file.type) xhr.setRequestHeader("Content-Type", file.type);
              xhr.upload.onprogress = (ev) => {
                if (!ev.lengthComputable) return;
                const prev = perFileLoaded.get(idx) ?? 0;
                progress.uploaded = progress.uploaded - prev + ev.loaded;
                perFileLoaded.set(idx, ev.loaded);
                emit();
              };
              xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                  // 최종 사이즈 보정 (마지막 progress 가 누락될 수 있음)
                  const prev = perFileLoaded.get(idx) ?? 0;
                  progress.uploaded = progress.uploaded - prev + file.size;
                  perFileLoaded.set(idx, file.size);
                  resolve();
                } else {
                  reject(new Error(`PUT ${xhr.status}: ${xhr.statusText}`));
                }
              };
              xhr.onerror = () => reject(new Error("network error"));
              xhr.onabort = () => reject(new Error("aborted"));
              ctrl.signal.addEventListener("abort", () => xhr.abort());
              xhr.send(file);
            });
          };
          try {
            await attempt();
          } catch (err) {
            // 1회 retry
            if (ctrl.signal.aborted) throw err;
            try {
              await attempt();
            } catch {
              failedIndices.push(idx);
              progress.errors.push({
                filename: file.name,
                error_message:
                  err instanceof Error ? err.message : "PUT failed",
              });
              emit();
              return;
            }
          }
          progress.uploadedFiles += 1;
          emit();
        }

        // 동시 N개 — semaphore-style
        const queue = files.map((f, i) => ({ idx: i, file: f }));
        const workers: Promise<void>[] = [];
        const next = async () => {
          while (queue.length > 0) {
            if (ctrl.signal.aborted) return;
            const item = queue.shift();
            if (!item) return;
            await putOne(item.idx, item.file);
          }
        };
        for (let i = 0; i < concurrency; i += 1) {
          workers.push(next());
        }
        await Promise.all(workers);

        if (ctrl.signal.aborted) {
          throw new Error("upload aborted");
        }

        // 3) finalize → arq enqueue
        progress.status = "finalizing";
        emit();
        await finalizeBulkUpload(sessionId, failedIndices);
        progress.status = "processing";
        emit();

        // 4) status polling
        while (!ctrl.signal.aborted) {
          await new Promise((r) => setTimeout(r, pollIntervalMs));
          const status = await getBulkUploadStatus(sessionId);
          progress.processed = status.processed_files;
          progress.failed = status.failed_files;
          progress.errors = status.errors;
          if (status.status === "completed" || status.status === "failed") {
            progress.status = status.status;
            emit();
            return { sessionId, finalStatus: { ...progress } };
          }
          emit();
        }
        throw new Error("upload aborted");
      } finally {
        setPending(false);
        cancelRef.current = null;
      }
    },
    [concurrency, pollIntervalMs],
  );

  return { upload, cancel, isPending };
}
