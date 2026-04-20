"use client";

import { useMemo, useState, type FormEvent } from "react";



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
  useCreateDataSource,
  useDataSources,
  useDeleteDataSource,
  useTriggerDataSource,
  useUpdateDataSource,
} from "@/hooks/admin/useDataSources";
import type { DataSource, DataSourceUpsertBody } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

const SOURCE_TYPES = [
  { id: "confluence", label: "Confluence" },
  { id: "git", label: "Git" },
  { id: "jira", label: "Jira" },
  { id: "slack", label: "Slack" },
  { id: "teams", label: "Teams" },
  { id: "gwiki", label: "Google Wiki" },
  { id: "file_upload", label: "파일 업로드" },
  { id: "crawl_result", label: "크롤 결과" },
];

const SCHEDULES = ["수동", "hourly", "daily", "weekly"];

const TYPE_ICON: Record<string, string> = {
  confluence: "📘",
  git: "🔧",
  jira: "🪪",
  teams: "💬",
  slack: "💬",
  gwiki: "📚",
  file_upload: "📄",
  crawl_result: "🔍",
};

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function DataSourcesClient() {
  const toast = useToast();
  const { data, isLoading, isError, error, refetch } = useDataSources();
  const trigger = useTriggerDataSource();
  const create = useCreateDataSource();
  const update = useUpdateDataSource();
  const del = useDeleteDataSource();
  const [triggering, setTriggering] = useState<string | null>(null);
  const [editing, setEditing] = useState<DataSource | null>(null);
  const [creating, setCreating] = useState(false);

  async function onTrigger(s: DataSource) {
    setTriggering(s.id);
    try {
      const res = await trigger.mutateAsync(s.id);
      toast.push(
        res.message || `'${s.name}' 동기화를 시작했습니다.`,
        "success",
      );
    } catch (e) {
      toast.push(
        e instanceof Error ? e.message : "동기화 트리거 실패",
        "danger",
      );
    } finally {
      setTriggering(null);
    }
  }

  async function onDelete(s: DataSource) {
    if (!confirm(`'${s.name}' 데이터 소스를 삭제하시겠습니까?\n인덱스에 적재된 문서는 유지되지만 향후 동기화는 중단됩니다.`))
      return;
    try {
      await del.mutateAsync(s.id);
      toast.push(`'${s.name}' 삭제됨`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  const counts = useMemo(() => {
    const sources = data ?? [];
    const byStatus = (s: DataSource) => (s.status || "").toLowerCase();
    return {
      total: sources.length,
      healthy: sources.filter((s) =>
        ["healthy", "active", "connected", "completed"].includes(byStatus(s)),
      ).length,
      syncing: sources.filter((s) =>
        ["syncing", "running"].includes(byStatus(s)),
      ).length,
      errored: sources.filter((s) =>
        ["error", "failed", "disconnected"].includes(byStatus(s)),
      ).length,
    };
  }, [data]);

  const columns: Column<DataSource>[] = [
    {
      key: "name",
      header: "이름",
      render: (s) => (
        <div className="flex items-center gap-2">
          <span aria-hidden>{TYPE_ICON[s.source_type] ?? "📁"}</span>
          <div className="flex flex-col">
            <span className="font-medium text-fg-default">{s.name}</span>
            <span className="font-mono text-[10px] text-fg-subtle">
              {s.source_type} · {s.id.slice(0, 8)}
            </span>
          </div>
        </div>
      ),
    },
    {
      key: "kb_id",
      header: "KB",
      render: (s) =>
        s.kb_id ? (
          <span className="font-mono text-[10px] text-fg-muted">{s.kb_id}</span>
        ) : (
          <span className="text-fg-subtle">—</span>
        ),
    },
    {
      key: "status",
      header: "상태",
      render: (s) => (
        <SeverityBadge level={statusToSeverity(s.status)}>
          {s.status || "unknown"}
        </SeverityBadge>
      ),
    },
    {
      key: "schedule",
      header: "스케줄",
      render: (s) => (
        <span className="text-fg-muted">{s.schedule || "수동"}</span>
      ),
    },
    {
      key: "last_sync_at",
      header: "마지막 동기화",
      render: (s) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(s.last_sync_at)}
        </span>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (s) => (
        <div className="flex justify-end gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setEditing(s)}
            disabled={triggering === s.id}
          >
            수정
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onDelete(s)}
            disabled={triggering === s.id}
          >
            삭제
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={triggering === s.id}
            onClick={() => onTrigger(s)}
          >
            {triggering === s.id ? "시작 중…" : "동기화"}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <section className="space-y-6">
      <header className="flex items-end justify-between">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold text-fg-default">
            데이터 소스
          </h1>
          <p className="text-sm text-fg-muted">
            외부 커넥터 (Confluence/Git/Jira/Slack/Teams/Wiki) 와 파일 업로드
            소스의 상태 및 동기화 관리.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => refetch()}>
            새로고침
          </Button>
          <Button size="sm" onClick={() => setCreating(true)}>
            + 신규 소스
          </Button>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="총 소스" value={counts.total} />
        <MetricCard
          label="정상"
          value={counts.healthy}
          tone={counts.healthy === counts.total ? "success" : "neutral"}
        />
        <MetricCard
          label="동기화 중"
          value={counts.syncing}
          tone={counts.syncing > 0 ? "warning" : "neutral"}
        />
        <MetricCard
          label="오류"
          value={counts.errored}
          tone={counts.errored > 0 ? "danger" : "neutral"}
        />
      </div>

      {isLoading ? (
        <Skeleton className="h-48" />
      ) : isError ? (
        <ErrorFallback
          title="데이터 소스 목록을 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : (
        <DataTable<DataSource>
          columns={columns}
          rows={data ?? []}
          rowKey={(r) => r.id}
          empty="데이터 소스가 없습니다. 신규 소스 등록이 필요합니다."
        />
      )}

      <DataSourceFormDialog
        // key 로 remount → editing 이 바뀌면 form state 새로 초기화
        key={editing?.id ?? (creating ? "create" : "closed")}
        open={creating || editing !== null}
        initial={editing}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
        onSubmit={async (body) => {
          try {
            if (editing) {
              await update.mutateAsync({ id: editing.id, body });
              toast.push("수정되었습니다", "success");
            } else {
              await create.mutateAsync(body);
              toast.push("신규 소스가 추가되었습니다", "success");
            }
            setCreating(false);
            setEditing(null);
          } catch (e) {
            toast.push(
              e instanceof Error ? e.message : "저장 실패",
              "danger",
            );
          }
        }}
        pending={create.isPending || update.isPending}
      />
    </section>
  );
}

function DataSourceFormDialog({
  open,
  initial,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  initial: DataSource | null;
  onClose: () => void;
  onSubmit: (body: DataSourceUpsertBody) => void;
  pending: boolean;
}) {
  // 부모가 key={initial?.id ?? 'create'} 로 remount 시켜주므로 lazy init 만 충분
  const [name, setName] = useState(initial?.name ?? "");
  const [sourceType, setSourceType] = useState(initial?.source_type ?? "git");
  const [kbId, setKbId] = useState(initial?.kb_id ?? "");
  const [schedule, setSchedule] = useState(initial?.schedule ?? "수동");
  const [crawlConfigText, setCrawlConfigText] = useState(
    JSON.stringify(initial?.crawl_config ?? {}, null, 2),
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    let crawl_config: Record<string, unknown> | null = null;
    try {
      crawl_config = crawlConfigText.trim() ? JSON.parse(crawlConfigText) : null;
    } catch {
      alert("crawl_config 가 유효한 JSON 이 아닙니다");
      return;
    }
    onSubmit({
      name: name.trim(),
      source_type: sourceType,
      kb_id: kbId.trim() || null,
      schedule: schedule === "수동" ? null : schedule,
      crawl_config,
    });
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={initial ? `데이터 소스 수정 — ${initial.name}` : "신규 데이터 소스"}
      description="외부 커넥터의 이름/타입/스케줄/크롤 설정 (JSON)"
      width="lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>
            취소
          </Button>
          <Button type="submit" form="ds-form" disabled={pending || !name.trim()}>
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form id="ds-form" onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            이름
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            타입
            <Select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value)}
            >
              {SOURCE_TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label} ({t.id})
                </option>
              ))}
            </Select>
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            대상 KB ID
            <Input
              value={kbId}
              onChange={(e) => setKbId(e.target.value)}
              placeholder="예: g-espa, AX_Role"
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            스케줄
            <Select
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
            >
              {SCHEDULES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          </label>
        </div>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          crawl_config (JSON)
          <Textarea
            value={crawlConfigText}
            onChange={(e) => setCrawlConfigText(e.target.value)}
            rows={8}
            placeholder='{"repo_url": "...", "include_globs": ["**/*.md"]}'
            className="font-mono text-xs"
          />
        </label>
      </form>
    </Dialog>
  );
}

