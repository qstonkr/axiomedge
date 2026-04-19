"use client";

import { Skeleton } from "@/components/ui";
import { useErrorReportsList } from "@/hooks/useFeedback";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, type Severity } from "./SeverityBadge";

type ErrorRow = Record<string, unknown>;

function fmtDate(s: unknown): string {
  if (typeof s !== "string") return "—";
  return s.slice(0, 19).replace("T", " ");
}

function priorityToSeverity(priority: unknown): Severity {
  const p = String(priority ?? "").toLowerCase();
  if (p === "critical") return "critical";
  if (p === "high") return "error";
  if (p === "medium") return "warn";
  if (p === "low") return "info";
  return "neutral";
}

export function ErrorsClient() {
  const list = useErrorReportsList({ page: 1, page_size: 100 });
  const items = (list.data?.items ?? []) as ErrorRow[];

  const pending = items.filter((r) => r.status === "pending").length;
  const resolved = items.filter((r) => r.status === "resolved").length;
  const critical = items.filter((r) => r.priority === "critical").length;

  const columns: Column<ErrorRow>[] = [
    {
      key: "priority",
      header: "우선순위",
      render: (r) => (
        <SeverityBadge level={priorityToSeverity(r.priority)}>
          {String(r.priority ?? "—")}
        </SeverityBadge>
      ),
    },
    {
      key: "error_type",
      header: "유형",
      render: (r) => (
        <span className="text-fg-muted">{String(r.error_type ?? "—")}</span>
      ),
    },
    {
      key: "title",
      header: "제목",
      render: (r) => (
        <span className="font-medium text-fg-default">
          {String(r.title ?? r.description ?? "—").slice(0, 80)}
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
      key: "status",
      header: "상태",
      render: (r) => {
        const s = String(r.status ?? "pending");
        return <SeverityBadge level={s === "resolved" ? "success" : "warn"}>{s}</SeverityBadge>;
      },
    },
    {
      key: "created_at",
      header: "신고 시각",
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
        <h1 className="text-xl font-semibold text-fg-default">오류 신고</h1>
        <p className="text-sm text-fg-muted">
          사용자가 신고한 잘못된 답변/문서. 운영자가 검토 후 resolve.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <MetricCard label="전체" value={list.data?.total ?? 0} />
        <MetricCard
          label="대기 중"
          value={pending}
          tone={pending > 0 ? "warning" : "neutral"}
        />
        <MetricCard label="해결됨" value={resolved} tone="success" />
        <MetricCard
          label="긴급 (critical)"
          value={critical}
          tone={critical > 0 ? "danger" : "neutral"}
        />
      </div>

      {list.isLoading ? (
        <Skeleton className="h-48" />
      ) : list.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          오류 신고를 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<ErrorRow>
          columns={columns}
          rows={items}
          rowKey={(r, idx) => String(r.id ?? r.report_id ?? `row-${idx}`)}
          empty="현재 미해결 오류 신고가 없습니다."
        />
      )}
    </section>
  );
}
