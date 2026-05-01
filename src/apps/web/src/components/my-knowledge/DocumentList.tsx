"use client";

import { FileText } from "lucide-react";

import { Skeleton } from "@/components/ui";
import type { KbDocument } from "@/lib/api/endpoints";

function fmtDate(s: unknown): string {
  if (typeof s !== "string") return "—";
  return s.slice(0, 19).replace("T", " ");
}

function docDisplayName(d: KbDocument): string {
  return (
    d.document_name ??
    d.document_id ??
    d.doc_id ??
    (typeof d.source === "string" ? d.source : "(이름 없음)")
  );
}

export function DocumentList({
  documents,
  total,
  isLoading,
  isError,
  errorMessage,
}: {
  documents: KbDocument[];
  total: number;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
}) {
  if (isLoading) {
    return (
      <section aria-labelledby="docs-heading" className="space-y-2">
        <h3 id="docs-heading" className="text-sm font-medium text-fg-default">
          업로드한 문서
        </h3>
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, idx) => (
            <Skeleton key={idx} className="h-12" />
          ))}
        </div>
      </section>
    );
  }

  if (isError) {
    return (
      <section aria-labelledby="docs-heading" className="space-y-2">
        <h3 id="docs-heading" className="text-sm font-medium text-fg-default">
          업로드한 문서
        </h3>
        <div className="rounded-md border border-danger-default/30 bg-danger-subtle p-3 text-sm">
          <div className="mb-1 font-medium text-danger-default">
            문서 목록을 불러올 수 없습니다
          </div>
          {errorMessage && (
            <p className="font-mono text-xs text-fg-muted">{errorMessage}</p>
          )}
        </div>
      </section>
    );
  }

  if (documents.length === 0) {
    return (
      <section aria-labelledby="docs-heading" className="space-y-2">
        <h3 id="docs-heading" className="text-sm font-medium text-fg-default">
          업로드한 문서
        </h3>
        <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
          아직 이 KB 에 업로드된 문서가 없습니다. 위 영역에서 파일을 끌어다
          놓으면 인제스트가 시작됩니다.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="docs-heading" className="space-y-2">
      <header className="flex items-baseline justify-between gap-2">
        <h3 id="docs-heading" className="text-sm font-medium text-fg-default">
          업로드한 문서
        </h3>
        <span className="text-xs text-fg-muted">총 {total}개</span>
      </header>
      <ul className="divide-y divide-border-default rounded-md border border-border-default bg-bg-canvas">
        {documents.map((d, idx) => {
          const key = d.document_id ?? d.doc_id ?? `${idx}`;
          const name = docDisplayName(d);
          const created = fmtDate(d.created_at);
          const chunks = typeof d.chunk_count === "number" ? d.chunk_count : null;
          return (
            <li
              key={key}
              className="flex items-center gap-3 px-3 py-2 text-sm"
            >
              <FileText aria-hidden size={14} strokeWidth={1.75} className="text-fg-muted" />
              <span className="flex-1 truncate text-fg-default" title={name}>
                {name}
              </span>
              {chunks !== null && (
                <span className="font-mono text-[10px] text-fg-subtle">
                  {chunks} chunks
                </span>
              )}
              <span className="font-mono text-[10px] text-fg-subtle">
                {created}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
