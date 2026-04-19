"use client";

import { useMemo, useState } from "react";

import { Button, Skeleton, useToast } from "@/components/ui";
import {
  useDataSources,
  useTriggerDataSource,
} from "@/hooks/admin/useDataSources";
import type { DataSource } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

const TYPE_ICON: Record<string, string> = {
  confluence: "📘",
  git: "🔧",
  jira: "🪪",
  teams: "💬",
  slack: "💬",
  gwiki: "📚",
  file_upload: "📄",
  crawl_result: "🔍",
};

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function DataSourcesClient() {
  const toast = useToast();
  const { data, isLoading, isError, error, refetch } = useDataSources();
  const trigger = useTriggerDataSource();
  const [triggering, setTriggering] = useState<string | null>(null);

  async function onTrigger(s: DataSource) {
    setTriggering(s.id);
    try {
      const res = await trigger.mutateAsync(s.id);
      toast.push(
        res.message || `'${s.name}' 동기화를 시작했습니다.`,
        "success",
      );
    } catch (e) {
      toast.push(
        e instanceof Error ? e.message : "동기화 트리거 실패",
        "danger",
      );
    } finally {
      setTriggering(null);
    }
  }

  const counts = useMemo(() => {
    const sources = data ?? [];
    const byStatus = (s: DataSource) => (s.status || "").toLowerCase();
    return {
      total: sources.length,
      healthy: sources.filter((s) =>
        ["healthy", "active", "connected", "completed"].includes(byStatus(s)),
      ).length,
      syncing: sources.filter((s) =>
        ["syncing", "running"].includes(byStatus(s)),
      ).length,
      errored: sources.filter((s) =>
        ["error", "failed", "disconnected"].includes(byStatus(s)),
      ).length,
    };
  }, [data]);

  const columns: Column<DataSource>[] = [
    {
      key: "name",
      header: "이름",
      render: (s) => (
        <div className="flex items-center gap-2">
          <span aria-hidden>{TYPE_ICON[s.source_type] ?? "📁"}</span>
          <div className="flex flex-col">
            <span className="font-medium text-fg-default">{s.name}</span>
            <span className="font-mono text-[10px] text-fg-subtle">
              {s.source_type} · {s.id.slice(0, 8)}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (s) =>
        s.kb_id ? (
          <span className="font-mono text-[10px] text-fg-muted">{s.kb_id}</span>
        ) : (
          <span className="text-fg-subtle">—</span>
        ),
    },
    {
      key: "status",
      header: "상태",
      render: (s) => (
        <SeverityBadge level={statusToSeverity(s.status)}>
          {s.status || "unknown"}
        </SeverityBadge>
      ),
    },
    {
      key: "schedule",
      header: "스케줄",
      render: (s) => (
        <span className="text-fg-muted">{s.schedule || "수동"}</span>
      ),
    },
    {
      key: "last_sync_at",
      header: "마지막 동기화",
      render: (s) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(s.last_sync_at)}
        </span>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (s) => (
        <Button
          size="sm"
          variant="secondary"
          disabled={triggering === s.id}
          onClick={() => onTrigger(s)}
        >
          {triggering === s.id ? "시작 중…" : "동기화"}
        </Button>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">
            데이터 소스
          </h1>
          <p className="text-sm text-fg-muted">
            외부 커넥터 (Confluence/Git/Jira/Slack/Teams/Wiki) 와 파일 업로드
            소스의 상태 및 동기화 관리.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          새로고침
        </Button>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="총 소스" value={counts.total} />
        <MetricCard
          label="정상"
          value={counts.healthy}
          tone={counts.healthy === counts.total ? "success" : "neutral"}
        />
        <MetricCard
          label="동기화 중"
          value={counts.syncing}
          tone={counts.syncing > 0 ? "warning" : "neutral"}
        />
        <MetricCard
          label="오류"
          value={counts.errored}
          tone={counts.errored > 0 ? "danger" : "neutral"}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            데이터 소스 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<DataSource>
          columns={columns}
          rows={data ?? []}
          rowKey={(r) => r.id}
          empty="데이터 소스가 없습니다. 신규 소스 등록이 필요합니다."
        />
      )}
    </section>
  );
}
