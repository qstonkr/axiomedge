"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  Dialog,
  ErrorFallback,
  Input,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  useAdminCreateErrorReport,
  useErrorReportsList,
  useResolveErrorReport,
} from "@/hooks/useFeedback";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, type Severity } from "./SeverityBadge";

type ErrorRow = Record<string, unknown>;

const ERROR_TYPES = [
  "incorrect_answer",
  "outdated",
  "incomplete",
  "duplicate",
  "broken_link",
  "formatting",
  "other",
] as const;

const PRIORITIES = ["critical", "high", "medium", "low"] as const;

function fmtDate(s: unknown): string {
  if (typeof s !== "string") return "—";
  return s.slice(0, 19).replace("T", " ");
}

function priorityToSeverity(priority: unknown): Severity {
  const p = String(priority ?? "").toLowerCase();
  if (p === "critical") return "critical";
  if (p === "high") return "error";
  if (p === "medium") return "warn";
  if (p === "low") return "info";
  return "neutral";
}

export function ErrorsClient() {
  const toast = useToast();
  const list = useErrorReportsList({ page: 1, page_size: 100 });
  const create = useAdminCreateErrorReport();
  const resolve = useResolveErrorReport();
  const [creating, setCreating] = useState(false);
  const [resolveTarget, setResolveTarget] = useState<ErrorRow | null>(null);
  const items = (list.data?.items ?? []) as ErrorRow[];

  const pending = items.filter((r) => r.status === "pending").length;
  const resolved = items.filter((r) => r.status === "resolved").length;
  const critical = items.filter((r) => r.priority === "critical").length;

  async function onResolveSubmit(reportId: string, note: string) {
    try {
      await resolve.mutateAsync({ reportId, body: { resolution_note: note } });
      toast.push("해결 처리됨", "success");
      setResolveTarget(null);
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "해결 실패", "danger");
    }
  }

  const columns: Column<ErrorRow>[] = [
    {
      key: "priority",
      header: "우선순위",
      render: (r) => (
        <SeverityBadge level={priorityToSeverity(r.priority)}>
          {String(r.priority ?? "—")}
        </SeverityBadge>
      ),
    },
    {
      key: "error_type",
      header: "유형",
      render: (r) => (
        <span className="text-fg-muted">{String(r.error_type ?? "—")}</span>
      ),
    },
    {
      key: "title",
      header: "제목",
      render: (r) => (
        <span className="font-medium text-fg-default">
          {String(r.title ?? r.description ?? "—").slice(0, 80)}
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
      key: "status",
      header: "상태",
      render: (r) => {
        const s = String(r.status ?? "pending");
        return (
          <SeverityBadge level={s === "resolved" ? "success" : "warn"}>
            {s}
          </SeverityBadge>
        );
      },
    },
    {
      key: "created_at",
      header: "신고 시각",
      render: (r) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(r.created_at)}
        </span>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (r) => {
        if (String(r.status ?? "pending") === "resolved") {
          return <span className="text-fg-subtle">—</span>;
        }
        return (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setResolveTarget(r)}
          >
            해결
          </Button>
        );
      },
    },
  ];

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">오류 신고</h1>
          <p className="text-sm text-fg-muted">
            사용자가 신고한 잘못된 답변/문서. 운영자가 검토 후 resolve. 운영자
            본인이 발견한 오류도 직접 신고 가능.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          + 운영자 신고
        </Button>
      </header>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <MetricCard label="전체" value={list.data?.total ?? 0} />
        <MetricCard
          label="대기 중"
          value={pending}
          tone={pending > 0 ? "warning" : "neutral"}
        />
        <MetricCard label="해결됨" value={resolved} tone="success" />
        <MetricCard
          label="긴급 (critical)"
          value={critical}
          tone={critical > 0 ? "danger" : "neutral"}
        />
      </div>

      {list.isLoading ? (
        <Skeleton className="h-48" />
      ) : list.isError ? (
        <ErrorFallback
          title="오류 신고를 불러올 수 없습니다"
          error={list.error}
          onRetry={() => list.refetch()}
        />
      ) : (
        <DataTable<ErrorRow>
          columns={columns}
          rows={items}
          rowKey={(r, idx) => String(r.id ?? r.report_id ?? `row-${idx}`)}
          empty="현재 미해결 오류 신고가 없습니다."
        />
      )}

      <AdminReportDialog
        open={creating}
        onClose={() => setCreating(false)}
        onSubmit={async (body) => {
          try {
            await create.mutateAsync(body);
            toast.push("신고가 등록되었습니다", "success");
            setCreating(false);
          } catch (e) {
            toast.push(
              e instanceof Error ? e.message : "신고 등록 실패",
              "danger",
            );
          }
        }}
        pending={create.isPending}
      />

      <ResolveDialog
        target={resolveTarget}
        onClose={() => setResolveTarget(null)}
        onSubmit={(note) =>
          onResolveSubmit(
            String(resolveTarget?.id ?? resolveTarget?.report_id ?? ""),
            note,
          )
        }
        pending={resolve.isPending}
      />
    </section>
  );
}

function AdminReportDialog({
  open,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (body: {
    document_id?: string | null;
    kb_id?: string | null;
    title: string;
    description: string;
    error_type: string;
    priority: string;
  }) => void;
  pending: boolean;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [errorType, setErrorType] =
    useState<(typeof ERROR_TYPES)[number]>("other");
  const [priority, setPriority] = useState<(typeof PRIORITIES)[number]>("medium");
  const [documentId, setDocumentId] = useState("");
  const [kbId, setKbId] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      title: title.trim(),
      description: description.trim(),
      error_type: errorType,
      priority,
      document_id: documentId.trim() || null,
      kb_id: kbId.trim() || null,
    });
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="운영자 오류 신고"
      width="md"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            type="submit"
            form="admin-report-form"
            disabled={pending || !title.trim() || !description.trim()}
          >
            {pending ? "등록 중…" : "등록"}
          </Button>
        </>
      }
    >
      <form id="admin-report-form" onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            유형
            <Select
              value={errorType}
              onChange={(e) =>
                setErrorType(e.target.value as (typeof ERROR_TYPES)[number])
              }
            >
              {ERROR_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </Select>
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            우선순위
            <Select
              value={priority}
              onChange={(e) =>
                setPriority(e.target.value as (typeof PRIORITIES)[number])
              }
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </Select>
          </label>
        </div>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          제목
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          설명
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={5}
            maxLength={5000}
            required
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            관련 문서 ID (선택)
            <Input
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value)}
              placeholder="document_id"
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            KB (선택)
            <Input
              value={kbId}
              onChange={(e) => setKbId(e.target.value)}
              placeholder="kb_id"
            />
          </label>
        </div>
      </form>
    </Dialog>
  );
}

function ResolveDialog({
  target,
  onClose,
  onSubmit,
  pending,
}: {
  target: ErrorRow | null;
  onClose: () => void;
  onSubmit: (note: string) => void;
  pending: boolean;
}) {
  const [note, setNote] = useState("");
  if (!target) return null;
  return (
    <Dialog
      open={true}
      onClose={onClose}
      title={`해결 처리 — ${String(target.title ?? target.description ?? "").slice(0, 60)}`}
      description="해결 노트를 남기면 사용자에게 표시됩니다."
      width="sm"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button
            onClick={() => {
              onSubmit(note);
              setNote("");
            }}
            disabled={pending}
          >
            {pending ? "처리 중…" : "해결 처리"}
          </Button>
        </>
      }
    >
      <label className="block space-y-1 text-xs font-medium text-fg-muted">
        해결 노트 (선택)
        <Textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={4}
          placeholder="어떻게 해결했는지 기록 — 사용자가 fix 내역을 볼 수 있습니다."
        />
      </label>
    </Dialog>
  );
}
