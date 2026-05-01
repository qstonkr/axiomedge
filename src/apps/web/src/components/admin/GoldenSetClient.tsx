"use client";

import { useState } from "react";
import { Check, Trash2, X } from "lucide-react";

import {
  Badge,
  Button,
  ErrorFallback,
  Input,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  useDeleteGoldenItem,
  useGoldenSet,
  useUpdateGoldenItem,
} from "@/hooks/admin/useQuality";
import type { GoldenItem } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

const STATUS_FILTERS = [
  { value: "", label: "전체" },
  { value: "approved", label: "승인" },
  { value: "pending", label: "대기" },
  { value: "rejected", label: "거부" },
] as const;

function statusTone(s: string | undefined): "success" | "warning" | "danger" | "neutral" {
  if (s === "approved") return "success";
  if (s === "pending") return "warning";
  if (s === "rejected") return "danger";
  return "neutral";
}

export function GoldenSetClient() {
  const toast = useToast();
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const { data, isLoading, isError, error, refetch } = useGoldenSet({
    page: 1,
    page_size: 100,
    status: statusFilter || undefined,
  });
  const del = useDeleteGoldenItem();
  const upd = useUpdateGoldenItem();

  const items = data?.items ?? [];
  const filtered = filter
    ? items.filter((g) =>
        (g.question + " " + (g.answer ?? "") + " " + (g.kb_id ?? ""))
          .toLowerCase()
          .includes(filter.toLowerCase()),
      )
    : items;

  // KB 별 분포 — server-side status filter 와 별개. 서버 응답 기준.
  const byKb = new Map<string, number>();
  items.forEach((g) =>
    byKb.set(g.kb_id ?? "—", (byKb.get(g.kb_id ?? "—") ?? 0) + 1),
  );

  // 상태별 카운트 — 사용자가 빠르게 검토할 수 있게 metric 카드에.
  const statusCounts = items.reduce<Record<string, number>>((acc, g) => {
    const k = g.status ?? "pending";
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});

  async function onDelete(g: GoldenItem) {
    if (!confirm(`삭제하시겠습니까?\n\n${g.question.slice(0, 80)}…`)) return;
    try {
      await del.mutateAsync(g.id);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  async function onChangeStatus(
    g: GoldenItem,
    next: "approved" | "rejected" | "pending",
  ) {
    try {
      await upd.mutateAsync({ itemId: g.id, body: { status: next } });
      const label =
        next === "approved" ? "승인" : next === "rejected" ? "거부" : "대기";
      toast.push(`${label}으로 변경되었습니다`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "상태 변경 실패", "danger");
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
      key: "status",
      header: "상태",
      render: (g) => (
        <Badge tone={statusTone(g.status)}>{g.status ?? "pending"}</Badge>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (g) => (
        <div className="flex justify-end gap-1">
          {g.status !== "approved" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={upd.isPending}
              onClick={() => onChangeStatus(g, "approved")}
              title="승인"
              aria-label="승인"
            >
              <Check size={14} strokeWidth={1.75} aria-hidden className="text-success-default" />
            </Button>
          )}
          {g.status !== "rejected" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={upd.isPending}
              onClick={() => onChangeStatus(g, "rejected")}
              title="거부"
              aria-label="거부"
            >
              <X size={14} strokeWidth={1.75} aria-hidden className="text-fg-muted" />
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onDelete(g)}
            title="삭제"
            aria-label="삭제"
          >
            <Trash2 size={14} strokeWidth={1.75} aria-hidden className="text-danger-default" />
          </Button>
        </div>
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

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="총 항목" value={data?.total ?? 0} />
        <MetricCard
          label="승인"
          value={statusCounts.approved ?? 0}
          tone="success"
        />
        <MetricCard
          label="대기"
          value={statusCounts.pending ?? 0}
          tone={(statusCounts.pending ?? 0) > 0 ? "warning" : "neutral"}
        />
        <MetricCard
          label="거부"
          value={statusCounts.rejected ?? 0}
          tone={(statusCounts.rejected ?? 0) > 0 ? "danger" : "neutral"}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          상태 필터
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          검색
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="질문/정답/KB 부분 일치"
          />
        </label>
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
        <ErrorFallback
          title="Golden Set 을 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
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
