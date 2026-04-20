"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorFallback, Input, Skeleton } from "@/components/ui";
import { useGraphSearch, useGraphStats } from "@/hooks/admin/useOps";
import type { GraphSearchHit } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { GraphView } from "./GraphView";
import { MetricCard } from "./MetricCard";

export function GraphClient() {
  const stats = useGraphStats();
  const [draft, setDraft] = useState("");
  const [committed, setCommitted] = useState("");
  const [selected, setSelected] = useState<GraphSearchHit | null>(null);
  const search = useGraphSearch({ query: committed });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setCommitted(draft.trim());
  }

  const totalNodes =
    stats.data?.total_nodes ??
    Object.values(stats.data?.node_types ?? {}).reduce((s, n) => s + n, 0);
  const totalEdges =
    stats.data?.total_edges ??
    Object.values(stats.data?.edge_types ?? {}).reduce((s, n) => s + n, 0);

  const topNodeTypes = Object.entries(stats.data?.node_types ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const topEdgeTypes = Object.entries(stats.data?.edge_types ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);

  const hits =
    search.data?.hits ?? search.data?.results ?? ([] as GraphSearchHit[]);

  const columns: Column<GraphSearchHit>[] = [
    {
      key: "entity_name",
      header: "엔티티",
      render: (h) => (
        <span className="font-medium text-fg-default">{h.entity_name}</span>
      ),
    },
    {
      key: "entity_type",
      header: "타입",
      render: (h) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {h.entity_type ?? "—"}
        </span>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (h) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {h.kb_id ?? "—"}
        </span>
      ),
    },
    {
      key: "related_count",
      header: "관계",
      align: "right",
      render: (h) => h.related_count ?? "—",
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">엔티티 탐색</h1>
        <p className="text-sm text-fg-muted">
          Neo4j 그래프 — 노드/엣지 통계 + 엔티티 이름 검색.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="총 노드" value={totalNodes.toLocaleString()} />
        <MetricCard label="총 엣지" value={totalEdges.toLocaleString()} />
        <MetricCard
          label="노드 타입 종류"
          value={Object.keys(stats.data?.node_types ?? {}).length}
        />
        <MetricCard
          label="엣지 타입 종류"
          value={Object.keys(stats.data?.edge_types ?? {}).length}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <h2 className="mb-3 text-sm font-medium text-fg-default">
            상위 노드 타입
          </h2>
          {stats.isLoading ? (
            <Skeleton className="h-32" />
          ) : (
            <ul className="space-y-1 text-xs">
              {topNodeTypes.map(([type, count]) => (
                <li key={type} className="flex justify-between">
                  <span className="font-mono text-fg-muted">{type}</span>
                  <span className="tabular-nums text-fg-default">
                    {count.toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </article>
        <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <h2 className="mb-3 text-sm font-medium text-fg-default">
            상위 엣지 타입
          </h2>
          {stats.isLoading ? (
            <Skeleton className="h-32" />
          ) : (
            <ul className="space-y-1 text-xs">
              {topEdgeTypes.map(([type, count]) => (
                <li key={type} className="flex justify-between">
                  <span className="font-mono text-fg-muted">{type}</span>
                  <span className="tabular-nums text-fg-default">
                    {count.toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </article>
      </div>

      <form onSubmit={onSubmit} className="flex items-end gap-3">
        <label className="block flex-1 space-y-1 text-xs font-medium text-fg-muted">
          엔티티 검색
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="예: 신촌점, 김재경, ESPA"
          />
        </label>
        <Button type="submit" disabled={!draft.trim()}>
          검색
        </Button>
      </form>

      {committed && (
        <article className="space-y-3">
          <h2 className="text-sm font-medium text-fg-default">
            검색 결과 ({hits.length})
          </h2>
          {search.isLoading ? (
            <Skeleton className="h-32" />
          ) : search.isError ? (
            <ErrorFallback
              title="검색에 실패했습니다"
              error={search.error}
              onRetry={() => search.refetch()}
            />
          ) : (
            <DataTable<GraphSearchHit>
              columns={columns}
              rows={hits}
              rowKey={(r, idx) => r.entity_id ?? `${r.entity_name}-${idx}`}
              onRowClick={(r) => setSelected(r)}
              empty="결과가 없습니다"
            />
          )}
        </article>
      )}

      {selected && (
        <article className="space-y-3">
          <header className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium text-fg-default">
              🕸️ {selected.entity_name} — 1-hop 이웃 그래프
            </h2>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSelected(null)}
            >
              닫기
            </Button>
          </header>
          <GraphView
            nodeId={selected.entity_id ?? selected.entity_name}
            hubLabel={selected.entity_name}
          />
        </article>
      )}
    </section>
  );
}
