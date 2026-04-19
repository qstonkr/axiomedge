"use client";

import { Skeleton } from "@/components/ui";
import { usePendingVerifications } from "@/hooks/admin/useContent";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

type VerificationRow = Record<string, unknown>;

function fmtDate(s: unknown): string {
  if (typeof s !== "string") return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function VerificationClient() {
  const { data, isLoading, isError, error } = usePendingVerifications();
  const items = (data ?? []) as VerificationRow[];

  const columns: Column<VerificationRow>[] = [
    {
      key: "title",
      header: "문서",
      render: (r) => (
        <span className="font-medium text-fg-default">
          {String(r.title ?? r.document_name ?? r.document_id ?? "—")}
        </span>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {String(r.kb_id ?? "—")}
        </span>
      ),
    },
    {
      key: "type",
      header: "타입",
      render: (r) => (
        <span className="text-fg-muted">
          {String(r.type ?? r.verification_type ?? "verification")}
        </span>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (r) => {
        const s = String(r.status ?? "pending");
        return (
          <SeverityBadge level={statusToSeverity(s)}>{s}</SeverityBadge>
        );
      },
    },
    {
      key: "created_at",
      header: "요청 시각",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(r.created_at)}
        </span>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">검증 대기</h1>
        <p className="text-sm text-fg-muted">
          ingestion gate 또는 owner 가 추가 검증을 요청한 문서 큐.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="대기 건수" value={items.length} />
        <MetricCard
          label="긴급 (24h+)"
          value={
            items.filter((r) => {
              const ts = r.created_at;
              if (typeof ts !== "string") return false;
              return Date.now() - new Date(ts).getTime() > 86_400_000;
            }).length
          }
          tone="warning"
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            검증 대기 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<VerificationRow>
          columns={columns}
          rows={items}
          rowKey={(r, idx) => String(r.id ?? r.document_id ?? `row-${idx}`)}
          empty="검증 대기 문서가 없습니다 — 깨끗합니다."
        />
      )}
    </section>
  );
}
