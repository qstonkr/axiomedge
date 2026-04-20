"use client";

import { useMemo, useState, useSyncExternalStore } from "react";

import { Button, Skeleton, useToast } from "@/components/ui";
import { usePendingVerifications } from "@/hooks/admin/useContent";
import { useVerificationVote } from "@/hooks/admin/useLifecycle";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

type VerificationRow = Record<string, unknown>;

const URGENT_THRESHOLD_MS = 86_400_000; // 24h
const NOW_REFRESH_MS = 60_000; // 1 분마다 "지금" 갱신 — 운영 대시보드 충분.

// React 19 Compiler 는 render / useMemo 안에서 Date.now() 같은 impure 호출을
// 금지한다. 표준 escape hatch 인 useSyncExternalStore 로 외부 시간을 구독.
function subscribeToTime(callback: () => void): () => void {
  const id = setInterval(callback, NOW_REFRESH_MS);
  return () => clearInterval(id);
}
function getNow(): number {
  return Date.now();
}
function useNow(): number {
  return useSyncExternalStore(subscribeToTime, getNow, getNow);
}

function fmtDate(s: unknown): string {
  if (typeof s !== "string") return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function VerificationClient() {
  const toast = useToast();
  const { data, isLoading, isError, error } = usePendingVerifications();
  const vote = useVerificationVote();
  const [voting, setVoting] = useState<string | null>(null);
  // `data` 자체가 매 render 마다 같은 reference 이므로 useMemo 의 dep 안정화에 충분.
  const items = useMemo(() => (data ?? []) as VerificationRow[], [data]);

  // "지금" 은 useSyncExternalStore 로 (1분 주기) — useMemo 안에서 Date.now() 직접
  // 호출은 React 19 Compiler 가 impure 로 차단.
  const now = useNow();
  const urgentCount = useMemo(() => {
    return items.filter((r) => {
      const ts = r.created_at;
      if (typeof ts !== "string") return false;
      return now - new Date(ts).getTime() > URGENT_THRESHOLD_MS;
    }).length;
  }, [items, now]);

  async function onVote(r: VerificationRow, voteType: "upvote" | "downvote") {
    const docId = String(r.document_id ?? r.id ?? "");
    if (!docId) return;
    setVoting(docId);
    try {
      const res = await vote.mutateAsync({
        docId,
        voteType,
        kbId: typeof r.kb_id === "string" ? r.kb_id : undefined,
      });
      const tone = voteType === "upvote" ? "success" : "warning";
      toast.push(
        `${voteType === "upvote" ? "👍 검증 통과" : "👎 검증 보류"}` +
          (res.new_kts_score
            ? ` (신뢰도: ${(res.new_kts_score * 100).toFixed(0)}%)`
            : ""),
        tone,
      );
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "투표 실패", "danger");
    } finally {
      setVoting(null);
    }
  }

  const columns: Column<VerificationRow>[] = [
    {
      key: "title",
      header: "문서",
      render: (r) => (
        <span className="font-medium text-fg-default">
          {String(r.title ?? r.document_name ?? r.document_id ?? "—")}
        </span>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {String(r.kb_id ?? "—")}
        </span>
      ),
    },
    {
      key: "type",
      header: "타입",
      render: (r) => (
        <span className="text-fg-muted">
          {String(r.type ?? r.verification_type ?? "verification")}
        </span>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (r) => {
        const s = String(r.status ?? "pending");
        return (
          <SeverityBadge level={statusToSeverity(s)}>{s}</SeverityBadge>
        );
      },
    },
    {
      key: "created_at",
      header: "요청 시각",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(r.created_at)}
        </span>
      ),
    },
    {
      key: "_actions",
      header: "투표",
      align: "right",
      render: (r) => {
        const docId = String(r.document_id ?? r.id ?? "");
        return (
          <div className="flex justify-end gap-1">
            <Button
              size="sm"
              variant="ghost"
              disabled={voting === docId}
              onClick={() => onVote(r, "upvote")}
            >
              👍
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={voting === docId}
              onClick={() => onVote(r, "downvote")}
            >
              👎
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">검증 대기</h1>
        <p className="text-sm text-fg-muted">
          ingestion gate 또는 owner 가 추가 검증을 요청한 문서 큐.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="대기 건수" value={items.length} />
        <MetricCard
          label="긴급 (24h+)"
          value={urgentCount}
          tone="warning"
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            검증 대기 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<VerificationRow>
          columns={columns}
          rows={items}
          rowKey={(r, idx) => String(r.id ?? r.document_id ?? `row-${idx}`)}
          empty="검증 대기 문서가 없습니다 — 깨끗합니다."
        />
      )}
    </section>
  );
}
