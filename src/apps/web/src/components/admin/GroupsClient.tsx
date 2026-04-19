"use client";

import { Badge, Skeleton } from "@/components/ui";
import { useSearchGroups } from "@/hooks/admin/useContent";
import type { SearchGroup } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function GroupsClient() {
  const { data, isLoading, isError, error } = useSearchGroups();
  const groups = data?.groups ?? [];

  const columns: Column<SearchGroup>[] = [
    {
      key: "name",
      header: "그룹 이름",
      render: (g) => (
        <div className="flex items-center gap-2">
          <span className="font-medium text-fg-default">{g.name}</span>
          {g.is_default && <Badge tone="accent">기본</Badge>}
        </div>
      ),
    },
    {
      key: "description",
      header: "설명",
      render: (g) => (
        <span className="line-clamp-1 text-fg-muted">
          {g.description || "—"}
        </span>
      ),
    },
    {
      key: "kb_ids",
      header: "포함 KB",
      render: (g) => (
        <div className="flex flex-wrap gap-1">
          {(g.kb_ids ?? []).map((id) => (
            <span
              key={id}
              className="rounded bg-bg-muted px-1.5 py-0.5 font-mono text-[10px] text-fg-default"
            >
              {id}
            </span>
          ))}
          {(g.kb_ids ?? []).length === 0 && (
            <span className="text-fg-subtle">—</span>
          )}
        </div>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">검색 그룹</h1>
        <p className="text-sm text-fg-muted">
          여러 KB 를 묶어 한 번에 검색할 수 있는 사용자 친화 명칭.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="총 그룹" value={groups.length} />
        <MetricCard
          label="기본 그룹"
          value={groups.filter((g) => g.is_default).length}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            검색 그룹을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<SearchGroup>
          columns={columns}
          rows={groups}
          rowKey={(r) => r.id}
          empty="검색 그룹이 없습니다"
        />
      )}
    </section>
  );
}
