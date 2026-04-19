"use client";

import { useState } from "react";

import { Button, Input, Skeleton, useToast } from "@/components/ui";
import { useDeleteGoldenItem, useGoldenSet } from "@/hooks/admin/useQuality";
import type { GoldenItem } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

export function GoldenSetClient() {
  const toast = useToast();
  const [filter, setFilter] = useState("");
  const { data, isLoading, isError, error } = useGoldenSet({
    page: 1,
    page_size: 100,
  });
  const del = useDeleteGoldenItem();

  const items = data?.items ?? [];
  const filtered = filter
    ? items.filter((g) =>
        (g.question + " " + (g.answer ?? "") + " " + (g.kb_id ?? ""))
          .toLowerCase()
          .includes(filter.toLowerCase()),
      )
    : items;

  // KB 별 분포
  const byKb = new Map<string, number>();
  items.forEach((g) =>
    byKb.set(g.kb_id ?? "—", (byKb.get(g.kb_id ?? "—") ?? 0) + 1),
  );

  async function onDelete(g: GoldenItem) {
    if (!confirm(`삭제하시겠습니까?\n\n${g.question.slice(0, 80)}…`)) return;
    try {
      await del.mutateAsync(g.id);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  const columns: Column<GoldenItem>[] = [
    {
      key: "kb_id",
      header: "KB",
      render: (g) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {g.kb_id ?? "—"}
        </span>
      ),
    },
    {
      key: "question",
      header: "질문",
      render: (g) => (
        <span className="line-clamp-2 text-fg-default">{g.question}</span>
      ),
    },
    {
      key: "answer",
      header: "정답",
      render: (g) => (
        <span className="line-clamp-2 text-fg-muted">{g.answer ?? "—"}</span>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (g) => (
        <Button size="sm" variant="ghost" onClick={() => onDelete(g)}>
          삭제
        </Button>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">Golden Set</h1>
        <p className="text-sm text-fg-muted">
          RAG 평가의 정답 Q&A. 평가 실행 시 retrieval + generated answer 와 비교.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="총 항목" value={data?.total ?? 0} />
        <MetricCard label="필터 적용" value={filtered.length} />
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            검색
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="질문/정답/KB 부분 일치"
            />
          </label>
        </div>
      </div>

      {byKb.size > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          {Array.from(byKb.entries()).map(([kb, n]) => (
            <span
              key={kb}
              className="rounded-full bg-bg-muted px-2.5 py-1 font-mono text-fg-default"
            >
              {kb} <span className="text-fg-subtle">({n})</span>
            </span>
          ))}
        </div>
      )}

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            Golden Set 을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<GoldenItem>
          columns={columns}
          rows={filtered}
          rowKey={(r) => r.id}
          empty={filter ? "검색 결과 없음" : "Golden Set 항목이 없습니다"}
        />
      )}
    </section>
  );
}
