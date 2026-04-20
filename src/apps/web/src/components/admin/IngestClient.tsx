"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  ErrorFallback,
  Input,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  usePipelineStatus,
  useTriggerIngestion,
} from "@/hooks/admin/useContent";
import { useGatesBlocked, useGatesStats } from "@/hooks/admin/useLifecycle";
import { useSearchableKbs } from "@/hooks/useSearch";
import type {
  BlockedDocument,
  PipelineGateStat,
  TriggerIngestionBody,
} from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

const SOURCE_TYPES: TriggerIngestionBody["source_type"][] = [
  "CONFLUENCE",
  "JIRA",
  "GIT",
  "TEAMS",
  "GWIKI",
  "SHAREPOINT",
  "MANUAL",
];

function TriggerIngestionForm() {
  const toast = useToast();
  const trigger = useTriggerIngestion();
  const kbs = useSearchableKbs();
  const [kbId, setKbId] = useState("");
  const [sourceType, setSourceType] =
    useState<TriggerIngestionBody["source_type"]>("MANUAL");
  const [description, setDescription] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!kbId.trim()) {
      toast.push("KB 를 선택하거나 직접 입력하세요.", "warning");
      return;
    }
    try {
      const res = await trigger.mutateAsync({
        kb_id: kbId.trim(),
        source_type: sourceType,
        description: description.trim() || undefined,
      });
      const runId = res.run_id ?? res.id ?? "(id 없음)";
      toast.push(`인제스트가 시작되었습니다 — ${runId}`, "success");
      setDescription("");
    } catch (err) {
      toast.push(
        err instanceof Error ? err.message : "트리거 실패",
        "danger",
      );
    }
  }

  const kbOptions = kbs.data ?? [];

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-3 rounded-lg border border-border-default bg-bg-canvas p-4"
    >
      <h2 className="text-sm font-medium text-fg-default">수동 인제스트 트리거</h2>
      <p className="text-xs text-fg-muted">
        스케줄링 외 수동으로 한 번 실행할 때 사용. 진행 상태는 위 카드 +
        최근 실행 영역에서 확인.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          대상 KB
          {kbOptions.length > 0 ? (
            <Select value={kbId} onChange={(e) => setKbId(e.target.value)}>
              <option value="">— KB 선택 —</option>
              {kbOptions.map((kb) => (
                <option key={kb.kb_id} value={kb.kb_id}>
                  {kb.name} ({kb.kb_id})
                </option>
              ))}
            </Select>
          ) : (
            <Input
              value={kbId}
              onChange={(e) => setKbId(e.target.value)}
              placeholder="KB ID 직접 입력"
            />
          )}
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          소스 타입
          <Select
            value={sourceType}
            onChange={(e) =>
              setSourceType(
                e.target.value as TriggerIngestionBody["source_type"],
              )
            }
          >
            {SOURCE_TYPES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <label className="block space-y-1 text-xs font-medium text-fg-muted">
        설명 (선택)
        <Textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="인제스트 사유 — 운영 로그용"
          maxLength={500}
        />
      </label>
      <div className="flex justify-end">
        <Button type="submit" disabled={trigger.isPending}>
          {trigger.isPending ? "트리거 중…" : "인제스트 시작"}
        </Button>
      </div>
    </form>
  );
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function IngestClient() {
  const { data, isLoading, isError, error } = usePipelineStatus();
  const gates = useGatesStats();
  const blocked = useGatesBlocked();

  const gateColumns: Column<PipelineGateStat>[] = [
    {
      key: "gate",
      header: "게이트",
      render: (g) => (
        <span className="font-mono text-[10px] text-fg-default">
          {g.gate ?? g.gate_id ?? "—"}
        </span>
      ),
    },
    {
      key: "total_checks",
      header: "전체",
      align: "right",
      render: (g) => (g.total_checks ?? 0).toLocaleString(),
    },
    {
      key: "passed",
      header: "통과",
      align: "right",
      render: (g) => (g.passed ?? 0).toLocaleString(),
    },
    {
      key: "blocked",
      header: "차단",
      align: "right",
      render: (g) => (g.blocked ?? 0).toLocaleString(),
    },
    {
      key: "block_rate",
      header: "차단율",
      align: "right",
      render: (g) =>
        typeof g.block_rate === "number"
          ? `${(g.block_rate * 100).toFixed(1)}%`
          : "—",
    },
  ];

  const blockedColumns: Column<BlockedDocument>[] = [
    {
      key: "document_id",
      header: "문서 ID",
      render: (b) => (
        <span className="font-mono text-[10px] text-fg-default">
          {b.document_id ?? "—"}
        </span>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (b) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {b.kb_id ?? "—"}
        </span>
      ),
    },
    {
      key: "gate",
      header: "차단 게이트",
      render: (b) => (
        <SeverityBadge level="warn">{b.gate ?? "—"}</SeverityBadge>
      ),
    },
    {
      key: "reason",
      header: "사유",
      render: (b) => (
        <span className="line-clamp-2 text-fg-default">{b.reason ?? "—"}</span>
      ),
    },
    {
      key: "blocked_at",
      header: "시각",
      render: (b) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(b.blocked_at)}
        </span>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">Ingest 작업</h1>
        <p className="text-sm text-fg-muted">
          파이프라인 실행 상태 + 최근 run + 큐 — 15초마다 자동 갱신.
        </p>
      </header>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, idx) => (
            <Skeleton key={idx} className="h-24" />
          ))}
        </div>
      ) : isError ? (
        <ErrorFallback
          title="파이프라인 상태를 불러올 수 없습니다"
          error={error}
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <MetricCard
              label="실행 중"
              value={data?.active_runs ?? 0}
              tone={(data?.active_runs ?? 0) > 0 ? "warning" : "neutral"}
            />
            <MetricCard
              label="대기열"
              value={data?.queued ?? 0}
              tone={(data?.queued ?? 0) > 5 ? "warning" : "neutral"}
            />
            <MetricCard
              label="전체 상태"
              value={
                <SeverityBadge level={statusToSeverity(data?.status)}>
                  {data?.status || "unknown"}
                </SeverityBadge>
              }
            />
          </div>

          <article className="space-y-2">
            <h2 className="text-sm font-medium text-fg-default">
              인제스션 게이트 통계
            </h2>
            {gates.isLoading ? (
              <Skeleton className="h-24" />
            ) : (
              <DataTable
                columns={gateColumns}
                rows={gates.data?.gates ?? []}
                rowKey={(r, idx) => r.gate ?? r.gate_id ?? `gate-${idx}`}
                empty="현재 활성 게이트 통계가 없습니다."
              />
            )}
          </article>

          <article className="space-y-2">
            <h2 className="text-sm font-medium text-fg-default">
              거부/보류 문서 ({(blocked.data ?? []).length})
            </h2>
            {blocked.isLoading ? (
              <Skeleton className="h-24" />
            ) : (
              <DataTable
                columns={blockedColumns}
                rows={blocked.data ?? []}
                rowKey={(r, idx) => r.document_id ?? `b-${idx}`}
                empty="거부/보류된 문서가 없습니다."
              />
            )}
          </article>

          <TriggerIngestionForm />

          {data?.last_run && (
            <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
              <h2 className="mb-3 text-sm font-medium text-fg-default">
                최근 실행
              </h2>
              <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                <div>
                  <dt className="text-fg-muted">KB</dt>
                  <dd className="mt-0.5 font-mono text-fg-default">
                    {data.last_run.kb_id || "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-fg-muted">소스</dt>
                  <dd className="mt-0.5 text-fg-default">
                    {data.last_run.source_name || "—"}{" "}
                    <span className="text-fg-subtle">
                      ({data.last_run.source_type || "—"})
                    </span>
                  </dd>
                </div>
                <div>
                  <dt className="text-fg-muted">상태</dt>
                  <dd className="mt-0.5">
                    <SeverityBadge level={statusToSeverity(data.last_run.status)}>
                      {data.last_run.status || "unknown"}
                    </SeverityBadge>
                  </dd>
                </div>
                <div>
                  <dt className="text-fg-muted">시작</dt>
                  <dd className="mt-0.5 font-mono text-fg-default">
                    {fmtDate(data.last_run.started_at)}
                  </dd>
                </div>
                <div className="sm:col-span-4">
                  <dt className="text-fg-muted">Run ID</dt>
                  <dd className="mt-0.5 break-all font-mono text-xs text-fg-subtle">
                    {data.last_run.id || data.last_run.run_id || "—"}
                  </dd>
                </div>
                {data.last_run.error_message && (
                  <div className="sm:col-span-4">
                    <dt className="text-fg-muted">오류</dt>
                    <dd className="mt-0.5 whitespace-pre-wrap font-mono text-xs text-danger-default">
                      {data.last_run.error_message}
                    </dd>
                  </div>
                )}
              </dl>
            </article>
          )}
        </>
      )}
    </section>
  );
}
