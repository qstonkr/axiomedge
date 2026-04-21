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

        // 2) concurrency-throttled PUT — single 또는 multipart 분기
        const failedIndices: number[] = [];
        // per-file 누적 byte (single 은 cumulative, multipart 는 chunk 별 합산)
        const perFileLoaded = new Map<number, number>();
        // multipart 파일별 part ETag list — finalize 시 backend complete 호출에 전달
        const multipartCompletes: {
          file_idx: number;
          upload_id: string;
          parts: { PartNumber: number; ETag: string }[];
        }[] = [];

        async function putWithProgress(
          url: string, body: Blob | File, idx: number,
          chunkOffset: number, fileSize: number,
        ): Promise<{ etag: string }> {
          return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("PUT", url, true);
            const contentType = body instanceof File ? body.type : "";
            if (contentType) xhr.setRequestHeader("Content-Type", contentType);
            xhr.upload.onprogress = (ev) => {
              if (!ev.lengthComputable) return;
              // multipart 케이스는 chunkOffset + ev.loaded 가 누적
              const cumulative = chunkOffset + ev.loaded;
              const prev = perFileLoaded.get(idx) ?? 0;
              progress.uploaded = progress.uploaded - prev + cumulative;
              perFileLoaded.set(idx, cumulative);
              emit();
            };
            xhr.onload = () => {
              if (xhr.status >= 200 && xhr.status < 300) {
                // 마지막 progress 보정 — chunkOffset + body.size 가 누적
                const cumulative = chunkOffset + body.size;
                const prev = perFileLoaded.get(idx) ?? 0;
                progress.uploaded = progress.uploaded - prev + cumulative;
                perFileLoaded.set(idx, cumulative);
                // multipart 의 경우 ETag 헤더 추출 (S3 가 따옴표 포함 반환)
                const etag = (xhr.getResponseHeader("ETag") || "").replace(
                  /"/g, "",
                );
                resolve({ etag });
              } else {
                reject(new Error(`PUT ${xhr.status}: ${xhr.statusText}`));
              }
            };
            xhr.onerror = () => reject(new Error("network error"));
            xhr.onabort = () => reject(new Error("aborted"));
            ctrl.signal.addEventListener("abort", () => xhr.abort());
            xhr.send(body);
            // unused fileSize — type system 만족용
            void fileSize;
          });
        }

        async function putOne(idx: number, file: File): Promise<void> {
          const entry = byIdx.get(idx);
          if (!entry) {
            failedIndices.push(idx);
            return;
          }

          if (entry.mode === "multipart" && entry.upload_id
              && entry.part_size && entry.presigned_part_urls) {
            // Multipart — chunk 별 PUT + ETag 수집
            const partSize = entry.part_size;
            const partUrls = entry.presigned_part_urls;
            const partCount = partUrls.length;
            const parts: { PartNumber: number; ETag: string }[] = [];
            for (let p = 0; p < partCount; p += 1) {
              if (ctrl.signal.aborted) throw new Error("aborted");
              const start = p * partSize;
              const end = Math.min(start + partSize, file.size);
              const blob = file.slice(start, end);
              const url = partUrls[p];
              const attempt = () =>
                putWithProgress(url, blob, idx, start, file.size);
              try {
                const { etag } = await attempt();
                parts.push({ PartNumber: p + 1, ETag: etag });
              } catch (err) {
                // 1회 retry — 같은 chunk 만 (single PUT 처럼 처음부터 X)
                if (ctrl.signal.aborted) throw err;
                try {
                  const { etag } = await attempt();
                  parts.push({ PartNumber: p + 1, ETag: etag });
                } catch {
                  failedIndices.push(idx);
                  progress.errors.push({
                    filename: file.name,
                    error_message:
                      err instanceof Error
                        ? `part ${p + 1} failed: ${err.message}`
                        : `part ${p + 1} failed`,
                  });
                  emit();
                  return;
                }
              }
            }
            // 모든 part 성공 — finalize 시 backend 가 complete 호출에 사용
            multipartCompletes.push({
              file_idx: idx,
              upload_id: entry.upload_id,
              parts,
            });
          } else if (entry.mode === "single" && entry.presigned_url) {
            // Single PUT
            const attempt = () =>
              putWithProgress(entry.presigned_url!, file, idx, 0, file.size);
            try {
              await attempt();
            } catch (err) {
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
          } else {
            failedIndices.push(idx);
            progress.errors.push({
              filename: file.name,
              error_message: `unknown upload mode: ${entry.mode}`,
            });
            emit();
            return;
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

        // 3) finalize → arq enqueue (multipart 파일은 part ETag list 포함)
        progress.status = "finalizing";
        emit();
        await finalizeBulkUpload(sessionId, failedIndices, multipartCompletes);
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
