"use client";

import { useState } from "react";

import { Input, Skeleton } from "@/components/ui";
import { useGlossary } from "@/hooks/admin/useContent";
import type { GlossaryTerm } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function GlossaryClient() {
  const [filter, setFilter] = useState("");
  const { data, isLoading, isError, error } = useGlossary({
    page: 1,
    page_size: 100,
  });

  const items = data?.items ?? [];
  const filtered = filter
    ? items.filter((t) =>
        (t.term + " " + (t.term_ko ?? "") + " " + (t.definition ?? ""))
          .toLowerCase()
          .includes(filter.toLowerCase()),
      )
    : items;

  const columns: Column<GlossaryTerm>[] = [
    {
      key: "term",
      header: "용어",
      render: (t) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{t.term}</span>
          {t.term_ko && (
            <span className="text-[10px] text-fg-subtle">{t.term_ko}</span>
          )}
        </div>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (t) => (
        <span className="font-mono text-[10px] text-fg-muted">{t.kb_id}</span>
      ),
    },
    {
      key: "definition",
      header: "정의",
      render: (t) => (
        <span className="line-clamp-2 text-fg-default">
          {t.definition || "—"}
        </span>
      ),
    },
    {
      key: "domain",
      header: "도메인",
      render: (t) => (
        <span className="text-fg-muted">{t.domain || "—"}</span>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">용어집</h1>
        <p className="text-sm text-fg-muted">
          KB 별 용어 정의 + 동의어. 검색 시 query expansion 에 사용.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="총 용어" value={data?.total ?? 0} />
        <MetricCard label="필터 적용" value={filtered.length} />
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            검색
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="용어/정의 부분 일치"
            />
          </label>
        </div>
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            용어집을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<GlossaryTerm>
          columns={columns}
          rows={filtered}
          rowKey={(r) => r.id}
          empty={filter ? "검색 결과가 없습니다" : "등록된 용어가 없습니다"}
        />
      )}
    </section>
  );
}
