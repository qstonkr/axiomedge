"use client";

import { Skeleton } from "@/components/ui";
import { useEdgeServers } from "@/hooks/admin/useOps";
import type { EdgeServer } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

function fmtBytes(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return "—";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)}GB`;
  return `${mb}MB`;
}

function isStale(lastHeartbeat: string | null | undefined): boolean {
  if (!lastHeartbeat) return true;
  const ts = new Date(lastHeartbeat).getTime();
  return Date.now() - ts > 5 * 60 * 1000; // 5분 이상 heartbeat 없음
}

export function EdgeClient() {
  const { data, isLoading, isError, error } = useEdgeServers();
  const servers = data ?? [];

  const counts = {
    total: servers.length,
    online: servers.filter((s) => s.status === "online" && !isStale(s.last_heartbeat)).length,
    pending: servers.filter((s) => s.status === "pending").length,
    stale: servers.filter((s) => isStale(s.last_heartbeat) && s.status !== "pending").length,
  };

  const columns: Column<EdgeServer>[] = [
    {
      key: "store_id",
      header: "Store",
      render: (s) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{s.display_name ?? s.store_id}</span>
          <span className="font-mono text-[10px] text-fg-subtle">{s.store_id}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (s) => {
        const stale = isStale(s.last_heartbeat) && s.status !== "pending";
        return (
          <SeverityBadge level={stale ? "warn" : statusToSeverity(s.status)}>
            {stale ? "stale" : (s.status ?? "—")}
          </SeverityBadge>
        );
      },
    },
    {
      key: "last_heartbeat",
      header: "마지막 heartbeat",
      render: (s) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(s.last_heartbeat)}
        </span>
      ),
    },
    {
      key: "model_version",
      header: "모델",
      render: (s) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {s.model_version ?? "—"}
        </span>
      ),
    },
    {
      key: "ram",
      header: "RAM",
      align: "right",
      render: (s) =>
        s.ram_used_mb !== undefined && s.ram_total_mb
          ? `${fmtBytes(s.ram_used_mb)}/${fmtBytes(s.ram_total_mb)}`
          : "—",
    },
    {
      key: "disk_free_mb",
      header: "여유 디스크",
      align: "right",
      render: (s) => fmtBytes(s.disk_free_mb),
    },
    {
      key: "avg_latency_ms",
      header: "평균 지연",
      align: "right",
      render: (s) =>
        typeof s.avg_latency_ms === "number" ? `${s.avg_latency_ms}ms` : "—",
    },
    {
      key: "total_queries",
      header: "처리 쿼리",
      align: "right",
      render: (s) => (s.total_queries ?? 0).toLocaleString(),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">Edge 모델</h1>
        <p className="text-sm text-fg-muted">
          매장 edge server fleet — 30초마다 heartbeat 확인 + 자동 갱신.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="총 서버" value={counts.total} />
        <MetricCard label="온라인" value={counts.online} tone="success" />
        <MetricCard
          label="대기 중"
          value={counts.pending}
          tone={counts.pending > 0 ? "warning" : "neutral"}
        />
        <MetricCard
          label="Stale (5분+)"
          value={counts.stale}
          tone={counts.stale > 0 ? "danger" : "neutral"}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            edge 서버 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<EdgeServer>
          columns={columns}
          rows={servers}
          rowKey={(r) => r.id}
          empty="등록된 edge 서버가 없습니다"
        />
      )}
    </section>
  );
}
