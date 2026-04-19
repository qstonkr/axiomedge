import { Skeleton } from "@/components/ui";

import { MetaSignals } from "./MetaSignals";
import { SourceCard } from "./SourceCard";
import type { ChunkSource, Turn } from "./types";

export function ChatMessages({
  turns,
  pending,
  pendingQuery,
  onReportError,
}: {
  turns: Turn[];
  pending: boolean;
  pendingQuery: string | null;
  onReportError: (chunk: ChunkSource) => void;
}) {
  return (
    <ol className="flex flex-col gap-6">
      {turns.map((turn) =>
        turn.kind === "user" ? (
          <li key={turn.id} className="self-end">
            <div className="rounded-lg bg-accent-subtle px-4 py-2 text-sm text-fg-default shadow-xs">
              {turn.query}
            </div>
          </li>
        ) : (
          <li key={turn.id} className="space-y-3">
            <article className="rounded-lg border border-border-default bg-bg-canvas p-4 shadow-sm">
              <h3 className="sr-only">답변</h3>
              {turn.answer ? (
                <p className="whitespace-pre-wrap text-sm leading-7 text-fg-default">
                  {turn.answer}
                </p>
              ) : (
                <div className="space-y-2 text-sm leading-7">
                  <p className="text-fg-muted">
                    답변이 비어 있습니다 — 백엔드(LLM/agentic)가 응답을 생성하지 못했습니다.
                    {turn.chunks.length > 0 && " 소스 문서가 있다면 아래에서 직접 확인해 주세요."}
                  </p>
                  {turn.failure_reason && (
                    <div className="rounded-md border border-danger-default/30 bg-danger-subtle px-3 py-2 text-xs">
                      <div className="mb-1 font-medium text-danger-default">실패 원인</div>
                      <div className="whitespace-pre-wrap break-words font-mono text-fg-default">
                        {turn.failure_reason}
                      </div>
                    </div>
                  )}
                  {turn.errors && turn.errors.length > 1 && (
                    <details className="text-xs text-fg-subtle">
                      <summary className="cursor-pointer">
                        추가 오류 {turn.errors.length - 1}건
                      </summary>
                      <ul className="mt-1 list-disc space-y-1 pl-5 font-mono">
                        {turn.errors.slice(1).map((e, i) => (
                          <li key={i} className="break-words">{e}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  {turn.meta?.trace_id && (
                    <span className="mt-1 block font-mono text-xs text-fg-subtle">
                      trace: {turn.meta.trace_id}
                    </span>
                  )}
                </div>
              )}
              <div className="mt-3">
                <MetaSignals meta={turn.meta} />
              </div>
            </article>
            {turn.chunks.length > 0 && (
              <details className="group rounded-lg border border-border-default bg-bg-subtle p-3 text-sm">
                <summary className="flex cursor-pointer list-none items-center justify-between text-fg-muted">
                  <span>
                    소스 문서 {turn.chunks.length}개
                    {turn.searched_kbs && turn.searched_kbs.length > 0 && (
                      <span className="ml-2 text-xs text-fg-subtle">
                        ({turn.searched_kbs.join(", ")})
                      </span>
                    )}
                  </span>
                  <span aria-hidden className="transition-transform group-open:rotate-180">
                    ▾
                  </span>
                </summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {turn.chunks.slice(0, 8).map((c, idx) => (
                    <SourceCard
                      key={c.id ?? idx}
                      chunk={c}
                      onReportError={onReportError}
                    />
                  ))}
                </div>
              </details>
            )}
          </li>
        ),
      )}
      {pending && (
        <li className="space-y-3" aria-live="polite">
          {pendingQuery && (
            <div className="self-end rounded-lg bg-accent-subtle px-4 py-2 text-sm text-fg-default shadow-xs">
              {pendingQuery}
            </div>
          )}
          <div className="rounded-lg border border-border-default bg-bg-canvas p-4 shadow-sm">
            <Skeleton className="mb-3 h-4 w-2/3" />
            <Skeleton className="mb-2 h-3 w-full" />
            <Skeleton className="h-3 w-5/6" />
          </div>
        </li>
      )}
    </ol>
  );
}
