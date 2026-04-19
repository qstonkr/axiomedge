"use client";

import { useState } from "react";

import { Select, Skeleton } from "@/components/ui";
import { useKbLifecycle } from "@/hooks/admin/useLifecycle";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { KbLifecycleEvent } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function LifecycleClient() {
  const { data: kbs } = useSearchableKbs();
  const [kbId, setKbId] = useState<string>("");
  const lifecycle = useKbLifecycle(kbId || null);

  const eventColumns: Column<KbLifecycleEvent>[] = [
    {
      key: "ts",
      header: "시각",
      render: (e) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(e.ts)}
        </span>
      ),
    },
    {
      key: "event",
      header: "이벤트",
      render: (e) => (
        <SeverityBadge level={statusToSeverity(e.event)}>
          {e.event ?? "—"}
        </SeverityBadge>
      ),
    },
    {
      key: "actor",
      header: "주체",
      render: (e) => (
        <span className="text-fg-muted">{e.actor ?? "system"}</span>
      ),
    },
    {
      key: "detail",
      header: "상세",
      render: (e) => (
        <span className="line-clamp-1 text-fg-default">{e.detail ?? "—"}</span>
      ),
    },
  ];

  const scheduledColumns: Column<{
    document_id: string;
    archive_at: string;
    reason?: string;
  }>[] = [
    {
      key: "document_id",
      header: "문서 ID",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-default">
          {r.document_id}
        </span>
      ),
    },
    {
      key: "archive_at",
      header: "예정 시각",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(r.archive_at)}
        </span>
      ),
    },
    {
      key: "reason",
      header: "사유",
      render: (r) => <span className="text-fg-muted">{r.reason ?? "—"}</span>,
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">
          문서 라이프사이클
        </h1>
        <p className="text-sm text-fg-muted">
          KB 별 문서 상태 (초안 → 게시 → 아카이브 → 삭제) + 자동 아카이브 예정
          + 상태 전이 이력.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4 sm:col-span-1">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            KB 선택
            <Select value={kbId} onChange={(e) => setKbId(e.target.value)}>
              <option value="">— KB 선택 —</option>
              {(kbs ?? []).map((kb) => (
                <option key={kb.kb_id} value={kb.kb_id}>
                  {kb.name} ({kb.kb_id})
                </option>
              ))}
            </Select>
          </label>
        </div>
        <MetricCard
          label="현재 단계"
          value={
            lifecycle.data ? (
              <SeverityBadge level={statusToSeverity(lifecycle.data.stage)}>
                {lifecycle.data.stage}
              </SeverityBadge>
            ) : (
              "—"
            )
          }
        />
        <MetricCard
          label="이벤트 수"
          value={lifecycle.data?.events?.length ?? 0}
        />
      </div>

      {!kbId ? (
        <div className="rounded-lg border border-dashed border-border-default bg-bg-subtle px-6 py-10 text-center text-sm text-fg-muted">
          KB 를 선택하면 라이프사이클이 표시됩니다.
        </div>
      ) : lifecycle.isLoading ? (
        <Skeleton className="h-48" />
      ) : lifecycle.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          라이프사이클을 불러올 수 없습니다
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <MetricCard
              label="초안"
              value={lifecycle.data?.draft_count ?? "—"}
            />
            <MetricCard
              label="게시"
              value={lifecycle.data?.published_count ?? "—"}
              tone="success"
            />
            <MetricCard
              label="아카이브"
              value={lifecycle.data?.archived_count ?? "—"}
              tone="warning"
            />
          </div>

          {(lifecycle.data?.scheduled_archive ?? []).length > 0 && (
            <article className="space-y-2">
              <h2 className="text-sm font-medium text-fg-default">
                자동 아카이브 예정 (
                {lifecycle.data?.scheduled_archive?.length})
              </h2>
              <DataTable
                columns={scheduledColumns}
                rows={lifecycle.data?.scheduled_archive ?? []}
                rowKey={(r, idx) => `${r.document_id}-${idx}`}
                empty="예정 없음"
              />
            </article>
          )}

          <article className="space-y-2">
            <h2 className="text-sm font-medium text-fg-default">상태 전이 이력</h2>
            <DataTable
              columns={eventColumns}
              rows={lifecycle.data?.events ?? []}
              rowKey={(_e, idx) => `evt-${idx}`}
              empty="이벤트 없음"
            />
          </article>
        </>
      )}
    </section>
  );
}
