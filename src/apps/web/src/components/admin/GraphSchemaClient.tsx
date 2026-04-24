"use client";

import { useState } from "react";

import { Badge, Button, Input, Skeleton, useToast } from "@/components/ui";
import {
  useApproveCandidate,
  useGraphSchemaCandidates,
  useMergeCandidate,
  useRejectCandidate,
  useRenameCandidate,
  useTriggerBootstrap,
  useTriggerReextract,
} from "@/hooks/admin/useGraphSchema";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { GraphSchemaCandidate } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";

const ADMIN_USER = "admin@web";

type Row = GraphSchemaCandidate & Record<string, unknown>;

export function GraphSchemaClient() {
  const toast = useToast();
  const { data: kbs } = useSearchableKbs();
  const [kbId, setKbId] = useState<string>("");
  const { data, isLoading, isError, error } = useGraphSchemaCandidates(kbId);

  const approve = useApproveCandidate(kbId);
  const reject = useRejectCandidate(kbId);
  const merge = useMergeCandidate(kbId);
  const rename = useRenameCandidate(kbId);
  const bootstrap = useTriggerBootstrap();
  const reextract = useTriggerReextract();

  async function onApprove(c: GraphSchemaCandidate) {
    if (!confirm(`'${c.label}' 을(를) 승인하시겠습니까?`)) return;
    try {
      await approve.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        approved_by: ADMIN_USER,
      });
      toast.push("승인 — YAML 업데이트됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "승인 실패", "danger");
    }
  }

  async function onReject(c: GraphSchemaCandidate) {
    const reason = prompt("거부 사유 (선택)", "");
    if (reason === null) return;
    try {
      await reject.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        decided_by: ADMIN_USER,
        reason: reason || undefined,
      });
      toast.push("거부됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "거부 실패", "danger");
    }
  }

  async function onMerge(c: GraphSchemaCandidate) {
    const target = prompt(`'${c.label}' 을(를) 어느 라벨로 병합?`, "");
    if (!target) return;
    try {
      await merge.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        merge_into: target,
        decided_by: ADMIN_USER,
      });
      toast.push(`병합 → '${target}'`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "병합 실패", "danger");
    }
  }

  async function onRename(c: GraphSchemaCandidate) {
    const next = prompt(`'${c.label}' 을(를) 어떤 이름으로 승인?`, c.label);
    if (!next || next === c.label) return;
    try {
      await rename.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        new_label: next,
        approved_by: ADMIN_USER,
      });
      toast.push(`승인 (이름 변경: '${next}')`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "이름 변경 실패", "danger");
    }
  }

  async function onBootstrap() {
    if (!kbId) return;
    try {
      await bootstrap.mutateAsync(kbId);
      toast.push("Bootstrap 큐 등록됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "Bootstrap 실패", "danger");
    }
  }

  async function onReextract() {
    if (!kbId) return;
    if (!confirm(`'${kbId}' 를 현재 스키마로 전체 재추출합니다. 계속?`)) return;
    try {
      const res = await reextract.mutateAsync({
        kb_id: kbId, triggered_by_user: ADMIN_USER,
      });
      toast.push(
        `재추출 큐 등록 (v${res.schema_version_from} → v${res.schema_version_to})`,
        "success",
      );
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "재추출 실패", "danger");
    }
  }

  const candidates: Row[] = (data?.candidates ?? []) as Row[];

  const columns: Column<Row>[] = [
    {
      key: "candidate_type",
      header: "종류",
      render: (c) => (
        <Badge tone={c.candidate_type === "node" ? "accent" : "neutral"}>
          {c.candidate_type}
        </Badge>
      ),
    },
    {
      key: "label",
      header: "라벨",
      render: (c) => (
        <span className="font-mono text-fg-default">{c.label}</span>
      ),
    },
    {
      key: "frequency",
      header: "빈도",
      align: "right",
      render: (c) => <span>{c.frequency}</span>,
    },
    {
      key: "confidence_avg",
      header: "신뢰도",
      render: (c) => (
        <span>
          {c.confidence_avg.toFixed(2)}{" "}
          <span className="text-fg-subtle">
            ({c.confidence_min.toFixed(2)}–{c.confidence_max.toFixed(2)})
          </span>
        </span>
      ),
    },
    {
      key: "similar_labels",
      header: "유사 라벨",
      render: (c) => (
        <span className="text-fg-muted">
          {c.similar_labels.length > 0
            ? c.similar_labels
                .map((s) =>
                  typeof s === "object" && s !== null && "label" in s
                    ? String((s as { label: unknown }).label)
                    : "",
                )
                .filter(Boolean)
                .join(", ")
            : "—"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "작업",
      render: (c) => (
        <div className="flex gap-1">
          <Button size="sm" variant="primary" onClick={() => onApprove(c)}>
            승인
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onRename(c)}>
            이름
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onMerge(c)}>
            병합
          </Button>
          <Button size="sm" variant="danger" onClick={() => onReject(c)}>
            거부
          </Button>
        </div>
      ),
    },
  ];

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-fg-default">그래프 스키마</h1>
        <div className="flex items-center gap-2">
          <Input
            list="kb-options"
            placeholder="kb_id"
            value={kbId}
            onChange={(e) => setKbId(e.target.value)}
            className="w-40"
          />
          <datalist id="kb-options">
            {(kbs ?? []).map((k) => (
              <option key={k.id} value={k.id} />
            ))}
          </datalist>
          <Button
            size="sm"
            variant="secondary"
            onClick={onBootstrap}
            disabled={!kbId || bootstrap.isPending}
          >
            Bootstrap
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={onReextract}
            disabled={!kbId || reextract.isPending}
          >
            재추출
          </Button>
        </div>
      </header>

      {!kbId && <p className="text-fg-muted">검토할 KB를 선택하세요.</p>}

      {kbId && isLoading && <Skeleton className="h-48 w-full" />}
      {kbId && isError && (
        <p className="text-danger-default">
          {error instanceof Error ? error.message : "불러오기 실패"}
        </p>
      )}
      {kbId && !isLoading && !isError && (
        <DataTable<Row>
          rows={candidates}
          columns={columns}
          rowKey={(r) => r.id}
          empty="대기 중인 후보가 없습니다."
        />
      )}
    </section>
  );
}
