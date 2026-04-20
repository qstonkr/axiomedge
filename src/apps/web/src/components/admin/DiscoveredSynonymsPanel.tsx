"use client";

import { Skeleton } from "@/components/ui";
import { useDiscoveredSynonyms } from "@/hooks/admin/useContent";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

type Row = {
  id: string;
  kb_id?: string;
  term?: string;
  synonyms?: string[];
  source?: string;
  status?: string;
  created_at?: string;
};

/**
 * 검색 패턴 / co-occurrence 분석으로 자동 발견된 동의어 후보 큐. 운영자가
 * 검토 후 승인/거부. (승인/거부 mutation 은 별도 backend endpoint 와
 * 연결돼 추후 단계에서 추가 — 현재는 큐 노출만.)
 */
export function DiscoveredSynonymsPanel() {
  const { data, isLoading } = useDiscoveredSynonyms({
    status: "pending",
    page: 1,
    page_size: 50,
  });

  const rows = (data?.items ?? []).map((it) => ({
    id: it.id,
    kb_id: it.kb_id,
    term: it.term,
    synonyms: it.synonyms ?? [],
    source: it.source ?? "auto_discovered",
    status: it.status ?? "pending",
    created_at: it.created_at,
  })) as Row[];

  const columns: Column<Row>[] = [
    {
      key: "term",
      header: "기준 용어",
      render: (r) => (
        <span className="font-medium text-fg-default">{r.term ?? "—"}</span>
      ),
    },
    {
      key: "synonyms",
      header: "후보 동의어",
      render: (r) => (
        <div className="flex flex-wrap gap-1">
          {(r.synonyms ?? []).slice(0, 6).map((s, idx) => (
            <span
              key={`${s}-${idx}`}
              className="rounded-full bg-bg-muted px-2 py-0.5 text-[11px] text-fg-default"
            >
              {s}
            </span>
          ))}
          {(r.synonyms?.length ?? 0) > 6 && (
            <span className="text-xs text-fg-subtle">
              +{(r.synonyms?.length ?? 0) - 6}
            </span>
          )}
        </div>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {r.kb_id ?? "—"}
        </span>
      ),
    },
    {
      key: "created_at",
      header: "발견 시각",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {(r.created_at ?? "").slice(0, 19).replace("T", " ")}
        </span>
      ),
    },
  ];

  return (
    <article className="space-y-3">
      <header className="space-y-1">
        <h3 className="text-sm font-medium text-fg-default">
          자동 발견된 동의어 후보
        </h3>
        <p className="text-xs text-fg-muted">
          검색 co-occurrence / cluster 분석으로 추론된 동의어 후보. 운영자
          검토 큐.
        </p>
      </header>
      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard
          label="대기 후보"
          value={data?.total ?? 0}
          tone={(data?.total ?? 0) > 0 ? "warning" : "neutral"}
        />
      </div>
      {isLoading ? (
        <Skeleton className="h-32" />
      ) : (
        <DataTable<Row>
          columns={columns}
          rows={rows}
          rowKey={(r) => r.id}
          empty="자동 발견된 동의어 후보가 없습니다."
        />
      )}
    </article>
  );
}
