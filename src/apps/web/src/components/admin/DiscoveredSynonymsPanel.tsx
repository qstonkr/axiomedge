"use client";

import { useState } from "react";

import { Button, Skeleton, useToast } from "@/components/ui";
import {
  useApproveDiscoveredSynonyms,
  useDiscoveredSynonyms,
  useRejectDiscoveredSynonyms,
} from "@/hooks/admin/useContent";

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
 * 검토 후 승인 → base term 의 synonyms 배열에 추가, 거부 → status=rejected.
 * 한 번에 여러 항목 일괄 처리 (체크박스 select).
 */
export function DiscoveredSynonymsPanel() {
  const toast = useToast();
  const { data, isLoading } = useDiscoveredSynonyms({
    status: "pending",
    page: 1,
    page_size: 50,
  });
  const approve = useApproveDiscoveredSynonyms();
  const reject = useRejectDiscoveredSynonyms();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const rows = (data?.items ?? []).map((it) => ({
    id: it.id,
    kb_id: it.kb_id,
    term: it.term,
    synonyms: it.synonyms ?? [],
    source: it.source ?? "auto_discovered",
    status: it.status ?? "pending",
    created_at: it.created_at,
  })) as Row[];

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    if (selected.size === rows.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(rows.map((r) => r.id)));
    }
  }

  async function onApprove() {
    if (selected.size === 0) return;
    try {
      const res = await approve.mutateAsync(Array.from(selected));
      toast.push(
        `${res.approved}건 승인됨${res.errors.length ? ` (실패 ${res.errors.length})` : ""}`,
        res.success ? "success" : "warning",
      );
      setSelected(new Set());
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "승인 실패", "danger");
    }
  }

  async function onReject() {
    if (selected.size === 0) return;
    if (!confirm(`${selected.size}건 거부하시겠습니까?`)) return;
    try {
      const res = await reject.mutateAsync(Array.from(selected));
      toast.push(
        `${res.rejected}건 거부됨${res.errors.length ? ` (실패 ${res.errors.length})` : ""}`,
        res.success ? "success" : "warning",
      );
      setSelected(new Set());
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "거부 실패", "danger");
    }
  }

  const allSelected = rows.length > 0 && selected.size === rows.length;
  const pending = approve.isPending || reject.isPending;

  const columns: Column<Row>[] = [
    {
      key: "_select",
      header: (
        <input
          type="checkbox"
          aria-label="전체 선택"
          checked={allSelected}
          onChange={toggleAll}
          className="h-3.5 w-3.5 accent-accent-default"
        />
      ),
      width: "32px",
      render: (r) => (
        <input
          type="checkbox"
          aria-label={`${r.term ?? r.id} 선택`}
          checked={selected.has(r.id)}
          onChange={() => toggle(r.id)}
          className="h-3.5 w-3.5 accent-accent-default"
        />
      ),
    },
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
          검토 큐 — 체크박스로 선택 후 일괄 승인/거부.
        </p>
      </header>
      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard
          label="대기 후보"
          value={data?.total ?? 0}
          tone={(data?.total ?? 0) > 0 ? "warning" : "neutral"}
        />
        <div className="flex items-end justify-end gap-2">
          <span className="self-center text-xs text-fg-muted">
            {selected.size > 0 ? `${selected.size}건 선택됨` : ""}
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={onApprove}
            disabled={selected.size === 0 || pending}
            title="선택한 후보를 base term 의 동의어로 등록"
          >
            ✅ 승인
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onReject}
            disabled={selected.size === 0 || pending}
            title="선택한 후보를 거부 (status=rejected)"
          >
            ❌ 거부
          </Button>
        </div>
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
