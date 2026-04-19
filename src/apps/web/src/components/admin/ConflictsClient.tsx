"use client";

import { useState } from "react";

import { Button, Skeleton, useToast } from "@/components/ui";
import {
  useDedupConflicts,
  useResolveDedupConflict,
} from "@/hooks/admin/useContent";
import type { DedupConflict } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

export function ConflictsClient() {
  const toast = useToast();
  const { data, isLoading, isError, error } = useDedupConflicts({
    page: 1,
    page_size: 100,
  });
  const resolve = useResolveDedupConflict();
  const [resolving, setResolving] = useState<string | null>(null);
  const items = data?.items ?? [];

  async function onResolve(c: DedupConflict, action: "keep_a" | "keep_b" | "merge" | "ignore") {
    if (!c.id) return;
    setResolving(c.id);
    try {
      await resolve.mutateAsync({ conflict_id: c.id, resolution: action });
      toast.push("해결 처리됨", "success");
    } catch (e) {
      toast.push(
        e instanceof Error ? e.message : "해결 처리 실패",
        "danger",
      );
    } finally {
      setResolving(null);
    }
  }

  const columns: Column<DedupConflict>[] = [
    {
      key: "kb_id",
      header: "KB",
      render: (c) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {c.kb_id || "—"}
        </span>
      ),
    },
    {
      key: "doc_a",
      header: "문서 A",
      render: (c) => (
        <span className="font-mono text-[10px] text-fg-default">
          {c.doc_a || "—"}
        </span>
      ),
    },
    {
      key: "doc_b",
      header: "문서 B",
      render: (c) => (
        <span className="font-mono text-[10px] text-fg-default">
          {c.doc_b || "—"}
        </span>
      ),
    },
    {
      key: "similarity",
      header: "유사도",
      align: "right",
      render: (c) =>
        typeof c.similarity === "number"
          ? `${(c.similarity * 100).toFixed(1)}%`
          : "—",
    },
    {
      key: "conflict_type",
      header: "타입",
      render: (c) => (
        <span className="text-fg-muted">{c.conflict_type || "duplicate"}</span>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (c) => (
        <SeverityBadge level={statusToSeverity(c.status)}>
          {c.status || "pending"}
        </SeverityBadge>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (c) =>
        c.status === "resolved" ? (
          <span className="text-fg-subtle">—</span>
        ) : (
          <div className="flex justify-end gap-1">
            <Button
              size="sm"
              variant="ghost"
              disabled={resolving === c.id}
              onClick={() => onResolve(c, "keep_a")}
            >
              A 유지
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={resolving === c.id}
              onClick={() => onResolve(c, "keep_b")}
            >
              B 유지
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={resolving === c.id}
              onClick={() => onResolve(c, "merge")}
            >
              병합
            </Button>
          </div>
        ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">중복/모순</h1>
        <p className="text-sm text-fg-muted">
          4-stage dedup 이 감지한 의심스러운 문서 페어. 운영자가 검토 후 해결.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <MetricCard label="전체" value={data?.total ?? 0} />
        <MetricCard
          label="대기"
          value={items.filter((c) => c.status === "pending").length}
          tone={
            items.filter((c) => c.status === "pending").length > 0
              ? "warning"
              : "neutral"
          }
        />
        <MetricCard
          label="해결됨"
          value={items.filter((c) => c.status === "resolved").length}
          tone="success"
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            충돌 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<DedupConflict>
          columns={columns}
          rows={items}
          rowKey={(r, idx) => r.id ?? `row-${idx}`}
          empty="현재 의심 페어가 없습니다 — 깔끔!"
        />
      )}
    </section>
  );
}
