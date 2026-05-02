"use client";

import { useState, type FormEvent } from "react";
import { AlertTriangle, CheckCircle2, Network, Search } from "lucide-react";

import {
  Button,
  ErrorFallback,
  Input,
  Skeleton,
  Tabs,
  useToast,
} from "@/components/ui";
import {
  useGraphExperts,
  useGraphIntegrity,
  useGraphSearch,
  useGraphStats,
  useRunGraphIntegrityCheck,
} from "@/hooks/admin/useOps";
import type { GraphExpert, GraphSearchHit } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { GraphView } from "./GraphView";
import { MetricCard } from "./MetricCard";
import { SeverityBadge } from "./SeverityBadge";

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

  // searchGraphEntities 가 backend `entities` → frontend `hits` 로 transform.
  const hits = search.data?.hits ?? ([] as GraphSearchHit[]);

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

      <Tabs
        items={[
          {
            id: "search",
            label: "엔티티 검색",
            content: (
              <div className="space-y-4">
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
                        rowKey={(r, idx) =>
                          r.entity_id ?? `${r.entity_name}-${idx}`
                        }
                        onRowClick={(r) => setSelected(r)}
                        empty="결과가 없습니다"
                      />
                    )}
                  </article>
                )}

                {selected && (
                  <article className="space-y-3">
                    <header className="flex items-center justify-between gap-3">
                      <h2 className="inline-flex items-center gap-1.5 text-sm font-medium text-fg-default">
                        <Network size={14} strokeWidth={1.75} aria-hidden />
                        {selected.entity_name} — 1-hop 이웃 그래프
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
                      hubType={selected.entity_type}
                    />
                  </article>
                )}
              </div>
            ),
          },
          {
            id: "experts",
            label: "전문가 찾기",
            content: <ExpertSearchPanel />,
          },
          {
            id: "integrity",
            label: "무결성 검사",
            content: <IntegrityPanel />,
          },
        ]}
      />
    </section>
  );
}

function ExpertSearchPanel() {
  const [draft, setDraft] = useState("");
  const [topic, setTopic] = useState("");
  const experts = useGraphExperts(topic);

  const items = experts.data?.experts ?? [];

  const columns: Column<GraphExpert>[] = [
    {
      key: "name",
      header: "전문가",
      render: (e) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{e.name ?? e.id}</span>
          {e.email && (
            <span className="text-[10px] text-fg-subtle">{e.email}</span>
          )}
        </div>
      ),
    },
    {
      key: "topics",
      header: "관련 topic",
      render: (e) => (
        <div className="flex flex-wrap gap-1">
          {(e.topics ?? []).slice(0, 5).map((t) => (
            <span
              key={t}
              className="rounded-full bg-bg-muted px-2 py-0.5 text-[11px] text-fg-default"
            >
              {t}
            </span>
          ))}
        </div>
      ),
    },
    {
      key: "trust_score",
      header: "신뢰도",
      align: "right",
      render: (e) =>
        typeof e.trust_score === "number"
          ? `${(e.trust_score * 100).toFixed(0)}%`
          : "—",
    },
    {
      key: "document_count",
      header: "담당 문서",
      align: "right",
      render: (e) => (e.document_count ?? 0).toLocaleString(),
    },
  ];

  return (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setTopic(draft.trim());
        }}
        className="flex items-end gap-3"
      >
        <label className="block flex-1 space-y-1 text-xs font-medium text-fg-muted">
          topic / 도메인
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="예: 결제 시스템, PBU, 신촌점"
          />
        </label>
        <Button type="submit" disabled={!draft.trim()}>
          전문가 찾기
        </Button>
      </form>

      {topic && (
        <article className="space-y-2">
          <h3 className="text-sm font-medium text-fg-default">
            결과 ({items.length})
          </h3>
          {experts.isLoading ? (
            <Skeleton className="h-32" />
          ) : experts.isError ? (
            <ErrorFallback
              title="전문가 검색 실패"
              error={experts.error}
              onRetry={() => experts.refetch()}
            />
          ) : (
            <DataTable<GraphExpert>
              columns={columns}
              rows={items}
              rowKey={(r, idx) => r.id ?? r.email ?? `e-${idx}`}
              empty="해당 topic 에 등록된 전문가가 없습니다."
            />
          )}
          {experts.data?.error && (
            <p className="inline-flex items-center gap-1 rounded-md border border-warning-default/30 bg-warning-subtle px-3 py-2 text-xs text-warning-default">
              <AlertTriangle size={12} strokeWidth={1.75} aria-hidden />
              {experts.data.error}
            </p>
          )}
        </article>
      )}
    </div>
  );
}

function IntegrityPanel() {
  const toast = useToast();
  const integrity = useGraphIntegrity();
  const run = useRunGraphIntegrityCheck();

  async function onRun() {
    try {
      const res = await run.mutateAsync(undefined);
      toast.push(
        `점검 완료 — orphan ${res.orphan_count}, missing ${res.missing_relationships}, inconsistencies ${res.inconsistencies}`,
        res.success ? "success" : "warning",
      );
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "점검 실패", "danger");
    }
  }

  const data = integrity.data;
  const issues = data?.issues ?? [];

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-fg-default">
            그래프 무결성 검사
          </h3>
          <p className="text-xs text-fg-muted">
            orphan 노드 · 누락된 관계 · 불일치 자동 점검. 실행 후 issue 별
            severity 와 설명.
          </p>
        </div>
        <Button
          onClick={onRun}
          disabled={run.isPending}
          size="sm"
          leftIcon={<Search aria-hidden size={12} strokeWidth={1.75} />}
        >
          {run.isPending ? "점검 중…" : "지금 점검"}
        </Button>
      </header>

      {integrity.isLoading ? (
        <Skeleton className="h-32" />
      ) : integrity.isError ? (
        <ErrorFallback
          title="무결성 보고를 불러올 수 없습니다"
          error={integrity.error}
          onRetry={() => integrity.refetch()}
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="상태"
              value={
                <SeverityBadge
                  level={data?.status === "ok" ? "success" : "warn"}
                >
                  {data?.status ?? "—"}
                </SeverityBadge>
              }
            />
            <MetricCard
              label="orphan 노드"
              value={data?.orphan_nodes ?? 0}
              tone={(data?.orphan_nodes ?? 0) > 0 ? "warning" : "neutral"}
            />
            <MetricCard
              label="dangling edge"
              value={data?.dangling_edges ?? 0}
              tone={(data?.dangling_edges ?? 0) > 0 ? "warning" : "neutral"}
            />
            <MetricCard
              label="누락 관계"
              value={data?.missing_relationships ?? 0}
              tone={
                (data?.missing_relationships ?? 0) > 0 ? "danger" : "neutral"
              }
            />
          </div>

          {data?.last_check && (
            <p className="text-xs text-fg-subtle">
              마지막 점검: {data.last_check}
            </p>
          )}

          {issues.length === 0 ? (
            <p className="inline-flex w-full items-center justify-center gap-1 rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
              {data?.status === "ok" ? (
                <>
                  <CheckCircle2 size={14} strokeWidth={1.75} aria-hidden className="text-success-default" />
                  그래프가 깨끗합니다 — issue 없음.
                </>
              ) : (
                "issue 데이터가 비어있습니다 — 위쪽 점검 버튼으로 실행하세요."
              )}
            </p>
          ) : (
            <ul className="space-y-2">
              {issues.map((iss, idx) => (
                <li
                  key={`${iss.type ?? "?"}-${idx}`}
                  className="rounded-md border border-warning-default/30 bg-warning-subtle px-4 py-3 text-sm"
                >
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <SeverityBadge
                      level={
                        iss.severity === "HIGH"
                          ? "error"
                          : iss.severity === "MEDIUM"
                            ? "warn"
                            : "info"
                      }
                    >
                      {iss.severity ?? "—"}
                    </SeverityBadge>
                    <span className="font-mono text-[10px] text-fg-muted">
                      {iss.type ?? "?"}
                    </span>
                  </div>
                  <p className="text-fg-default">
                    {iss.description ?? "(설명 없음)"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
