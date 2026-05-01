"use client";

import { useMemo, useState, type FormEvent } from "react";
import { AlertTriangle, KeyRound, Lock, Plus, Trash2 } from "lucide-react";

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
import { ConnectorCatalog } from "@/components/connectors/ConnectorCatalog";
import {
  useCreateDataSource,
  useDataSources,
  useDeleteDataSource,
  useTriggerDataSource,
  useUpdateDataSource,
} from "@/hooks/admin/useDataSources";
import type { DataSource, DataSourceUpsertBody } from "@/lib/api/endpoints";
import {
  CONNECTOR_CATALOG,
  findConnector,
  type ConnectorEntry,
} from "@/lib/connectors/catalog";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";
import { SharedTokensDialog } from "./SharedTokensDialog";

const SCHEDULES = ["수동", "hourly", "daily", "weekly"];

// connector 카탈로그의 icon SSOT 를 그대로 사용 — 신규 connector 추가 시
// catalog.ts 한 곳만 갱신하면 테이블 / 폼 / 카드 모두 자동 반영.
const TYPE_ICON: Record<string, string> = Object.fromEntries(
  CONNECTOR_CATALOG.map((c) => [c.id, c.icon]),
);

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
  // 신규 등록 워크플로우: 카탈로그 → 카드 선택 → form (preset).
  // catalogOpen=true 이면 카탈로그 카드 grid 가 떠 있고, 카드 선택 시
  // ``presetType`` 으로 source_type 을 채워 form dialog 가 열림.
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [presetType, setPresetType] = useState<string | null>(null);
  const [sharedTokensOpen, setSharedTokensOpen] = useState(false);

  function onPickConnector(entry: ConnectorEntry) {
    setCatalogOpen(false);
    setPresetType(entry.id);
  }

  function closeFormDialog() {
    setEditing(null);
    setPresetType(null);
  }

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
          <span aria-hidden>{TYPE_ICON[s.source_type] ?? "•"}</span>
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
      key: "has_secret",
      header: "토큰",
      render: (s) =>
        s.has_secret ? (
          <span
            className="inline-flex items-center text-success-default"
            title="SecretBox 에 토큰 저장됨"
            aria-label="토큰 저장됨"
          >
            <Lock size={14} strokeWidth={1.75} aria-hidden />
          </span>
        ) : (
          <span className="text-fg-subtle" title="토큰 미설정 — connector 가 401">
            —
          </span>
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
          <Button
            variant="ghost"
            size="sm"
            leftIcon={<KeyRound size={14} strokeWidth={1.75} aria-hidden />}
            onClick={() => setSharedTokensOpen(true)}
            title="Slack 같은 organization-wide bot 토큰 관리"
          >
            공유 토큰
          </Button>
          <Button
            size="sm"
            leftIcon={<Plus size={14} strokeWidth={1.75} aria-hidden />}
            onClick={() => setCatalogOpen(true)}
          >
            신규 소스
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

      <ConnectorCatalog
        open={catalogOpen}
        onClose={() => setCatalogOpen(false)}
        onSelect={onPickConnector}
        scope="admin"
        title="신규 데이터 소스 추가"
        description="추가할 connector 종류를 선택하세요. 회색 카드는 곧 출시 예정입니다."
      />

      <SharedTokensDialog
        open={sharedTokensOpen}
        onClose={() => setSharedTokensOpen(false)}
      />

      <DataSourceFormDialog
        // key 로 remount → editing 또는 presetType 이 바뀌면 form state 재초기화
        key={editing?.id ?? presetType ?? "closed"}
        open={editing !== null || presetType !== null}
        initial={editing}
        presetType={presetType}
        onClose={closeFormDialog}
        onSubmit={async (body) => {
          try {
            if (editing) {
              await update.mutateAsync({ id: editing.id, body });
              toast.push("수정되었습니다", "success");
            } else {
              await create.mutateAsync(body);
              toast.push("신규 소스가 추가되었습니다", "success");
            }
            closeFormDialog();
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
  presetType,
  onClose,
  onSubmit,
  pending,
}: {
  open: boolean;
  initial: DataSource | null;
  /** 신규 등록 시 카탈로그에서 선택한 source_type — initial 이 null 일 때만 사용. */
  presetType: string | null;
  onClose: () => void;
  onSubmit: (body: DataSourceUpsertBody) => void;
  pending: boolean;
}) {
  // 부모가 key={initial?.id ?? presetType ?? 'closed'} 로 remount 시켜주므로
  // lazy init 만 충분 — initial / presetType 가 바뀌면 새 인스턴스로 교체.
  const effectiveType = initial?.source_type ?? presetType ?? "git";
  const connector = findConnector(effectiveType);
  const [name, setName] = useState(initial?.name ?? "");
  const [kbId, setKbId] = useState(initial?.kb_id ?? "");
  const [schedule, setSchedule] = useState(initial?.schedule ?? "수동");
  const [crawlConfigText, setCrawlConfigText] = useState(
    initial?.crawl_config
      ? JSON.stringify(initial.crawl_config, null, 2)
      : "",  // 신규 등록은 빈 채로 — placeholder 가 example schema 안내
  );
  // Per-source token (Confluence PAT, Git auth_token, ...). Backend 는 plain
  // 절대 응답하지 않음 — has_secret bool 만. UI 는 password input.
  const [secretToken, setSecretToken] = useState("");
  const [clearSecret, setClearSecret] = useState(false);
  const hasExistingSecret = Boolean(initial?.has_secret);

  function submit(e: FormEvent) {
    e.preventDefault();
    let crawl_config: Record<string, unknown> | null = null;
    try {
      crawl_config = crawlConfigText.trim() ? JSON.parse(crawlConfigText) : null;
    } catch {
      alert("crawl_config 가 유효한 JSON 이 아닙니다");
      return;
    }
    // secret_token 처리:
    //   clearSecret=true → null 명시 (backend 가 SecretBox.delete)
    //   secretToken 값 입력 → 그 값 저장
    //   둘 다 아님 → key omit (옛 token 유지)
    const body: DataSourceUpsertBody = {
      name: name.trim(),
      source_type: effectiveType,
      kb_id: kbId.trim() || null,
      schedule: schedule === "수동" ? null : schedule,
      crawl_config,
    };
    if (clearSecret) {
      body.secret_token = null;
    } else if (secretToken.trim()) {
      body.secret_token = secretToken.trim();
    }
    onSubmit(body);
  }

  const titleText = initial
    ? `데이터 소스 수정 — ${initial.name}`
    : connector
      ? `${connector.icon} 신규 ${connector.label} 소스`
      : "신규 데이터 소스";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={titleText}
      description={
        connector?.description ?? "외부 커넥터의 이름/스케줄/크롤 설정 (JSON)"
      }
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
        {/* 타입은 카탈로그에서 선택됨 — form 안에서는 read-only 표시. 잘못
            선택했으면 취소 후 다시 카드에서 고름. */}
        <div className="rounded-md border border-border-default bg-bg-subtle px-3 py-2 text-xs text-fg-muted">
          <span className="text-fg-subtle">타입: </span>
          <span className="font-medium text-fg-default">
            {connector?.icon ?? "•"} {connector?.label ?? effectiveType}
          </span>
          <span className="font-mono text-[10px] text-fg-subtle">
            {" "}
            ({effectiveType})
          </span>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            이름
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
              placeholder={`예: ${connector?.label ?? "내 소스"} - prod`}
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            대상 KB ID
            <Input
              value={kbId}
              onChange={(e) => setKbId(e.target.value)}
              placeholder="예: g-espa, AX_Role"
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted sm:col-span-2">
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
            placeholder={
              connector?.configSchema ??
              '{"repo_url": "...", "include_globs": ["**/*.md"]}'
            }
            className="font-mono text-xs"
          />
          <span className="inline-flex items-start gap-1 text-[10px] text-fg-subtle">
            <AlertTriangle size={12} strokeWidth={1.75} aria-hidden className="mt-0.5 shrink-0" />
            <span>token / PAT / password 는 절대 여기에 넣지 마세요 — 아래 전용 입력 사용.</span>
          </span>
        </label>

        {/* Per-source secret (PAT / auth_token) — SecretBox 로 분리 저장 */}
        <fieldset className="space-y-2 rounded-md border border-warning-default/30 bg-warning-subtle/40 p-3">
          <legend className="inline-flex items-center gap-1 px-1 text-xs font-semibold text-warning-default">
            <Lock size={12} strokeWidth={1.75} aria-hidden />
            인증 토큰
          </legend>
          <p className="text-[11px] text-fg-muted">
            Confluence PAT / Git auth_token 등 — 입력한 값은 즉시 암호화되어
            저장됩니다 (DB 평문 X). 응답에는 절대 노출되지 않으며 동기화 시점에만
            connector 로 inject 됩니다.
          </p>
          <Input
            type="password"
            value={secretToken}
            onChange={(e) => {
              setSecretToken(e.target.value);
              if (e.target.value) setClearSecret(false);
            }}
            disabled={clearSecret}
            placeholder={
              hasExistingSecret
                ? "(설정됨 — 변경하려면 새 token 입력, 비워두면 유지)"
                : "토큰 입력 (선택 — 안 넣으면 connector 가 401)"
            }
            autoComplete="new-password"
          />
          {hasExistingSecret && (
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={clearSecret}
                onChange={(e) => {
                  setClearSecret(e.target.checked);
                  if (e.target.checked) setSecretToken("");
                }}
                className="h-3.5 w-3.5 accent-danger-default"
              />
              <span className="inline-flex items-center gap-1 text-danger-default">
                <Trash2 size={12} strokeWidth={1.75} aria-hidden />
                저장된 토큰 삭제 (저장 시 SecretBox 에서 제거)
              </span>
            </label>
          )}
        </fieldset>
      </form>
    </Dialog>
  );
}

