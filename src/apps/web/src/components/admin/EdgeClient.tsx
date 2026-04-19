"use client";

import { useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  Dialog,
  Input,
  Select,
  Skeleton,
  Tabs,
  Textarea,
  useToast,
  type TabItem,
} from "@/components/ui";
import { useEdgeServers } from "@/hooks/admin/useOps";
import {
  useBaseModels,
  useDeleteBaseModel,
  useDeleteBuild,
  useDeleteDistillProfile,
  useDeleteEdgeServer,
  useDeployBuild,
  useDistillBuilds,
  useDistillProfiles,
  useEdgeManifest,
  useRequestEdgeUpdate,
  useRollbackBuild,
  useTrainingDataStats,
  useTriggerGenerateTrainingData,
  useTriggerRetrain,
  useUpsertBaseModel,
} from "@/hooks/admin/useDistill";
import type {
  BaseModel,
  DistillBuild,
  DistillProfile,
  EdgeServer,
} from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";
import { SeverityBadge, statusToSeverity } from "./SeverityBadge";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

function fmtBytes(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return "—";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)}GB`;
  return `${mb}MB`;
}

function isStale(lastHeartbeat: string | null | undefined): boolean {
  if (!lastHeartbeat) return true;
  return Date.now() - new Date(lastHeartbeat).getTime() > 5 * 60 * 1000;
}

export function EdgeClient() {
  const items: TabItem[] = [
    { id: "servers", label: "서버", content: <ServersTab /> },
    { id: "profiles", label: "프로필", content: <ProfilesTab /> },
    { id: "base-models", label: "베이스 모델", content: <BaseModelsTab /> },
    { id: "builds", label: "학습/빌드", content: <BuildsTab /> },
    { id: "data", label: "데이터 큐레이션", content: <TrainingDataTab /> },
    { id: "ops", label: "운영/배포", content: <OperationsTab /> },
  ];
  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">Edge 모델</h1>
        <p className="text-sm text-fg-muted">
          매장 edge fleet + Distill 파이프라인 (프로필 → 데이터 → 학습 → 배포).
        </p>
      </header>
      <Tabs items={items} />
    </section>
  );
}

// ============================================================================
// 1. 서버 (edge fleet)
// ============================================================================

function ServersTab() {
  const toast = useToast();
  const { data, isLoading, isError } = useEdgeServers();
  const del = useDeleteEdgeServer();
  const update = useRequestEdgeUpdate();
  const servers = data ?? [];

  const counts = {
    total: servers.length,
    online: servers.filter((s) => s.status === "online" && !isStale(s.last_heartbeat)).length,
    pending: servers.filter((s) => s.status === "pending").length,
    stale: servers.filter((s) => isStale(s.last_heartbeat) && s.status !== "pending").length,
  };

  async function onDelete(s: EdgeServer) {
    if (!confirm(`Edge server '${s.store_id}' 등록 해제하시겠습니까?`)) return;
    try {
      await del.mutateAsync(s.store_id);
      toast.push("등록 해제됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  async function onRequestUpdate(s: EdgeServer) {
    try {
      await update.mutateAsync(s.store_id);
      toast.push(`'${s.store_id}' 업데이트 요청됨`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "요청 실패", "danger");
    }
  }

  const columns: Column<EdgeServer>[] = [
    {
      key: "store_id",
      header: "Store",
      render: (s) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">{s.display_name ?? s.store_id}</span>
          <span className="font-mono text-[10px] text-fg-subtle">{s.store_id}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (s) => {
        const stale = isStale(s.last_heartbeat) && s.status !== "pending";
        return (
          <SeverityBadge level={stale ? "warn" : statusToSeverity(s.status)}>
            {stale ? "stale" : (s.status ?? "—")}
          </SeverityBadge>
        );
      },
    },
    {
      key: "last_heartbeat",
      header: "Heartbeat",
      render: (s) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(s.last_heartbeat)}
        </span>
      ),
    },
    {
      key: "model_version",
      header: "모델",
      render: (s) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {s.model_version ?? "—"}
        </span>
      ),
    },
    {
      key: "ram",
      header: "RAM",
      align: "right",
      render: (s) =>
        s.ram_used_mb !== undefined && s.ram_total_mb
          ? `${fmtBytes(s.ram_used_mb)}/${fmtBytes(s.ram_total_mb)}`
          : "—",
    },
    {
      key: "avg_latency_ms",
      header: "지연",
      align: "right",
      render: (s) =>
        typeof s.avg_latency_ms === "number" ? `${s.avg_latency_ms}ms` : "—",
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (s) => (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" onClick={() => onRequestUpdate(s)}>
            업데이트 요청
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onDelete(s)}>
            등록 해제
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="총 서버" value={counts.total} />
        <MetricCard label="온라인" value={counts.online} tone="success" />
        <MetricCard label="대기 중" value={counts.pending} tone={counts.pending > 0 ? "warning" : "neutral"} />
        <MetricCard label="Stale (5분+)" value={counts.stale} tone={counts.stale > 0 ? "danger" : "neutral"} />
      </div>
      {isLoading ? <Skeleton className="h-48" /> : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          edge 서버 목록을 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<EdgeServer> columns={columns} rows={servers} rowKey={(r) => r.id} empty="등록된 edge 서버가 없습니다" />
      )}
    </div>
  );
}

// ============================================================================
// 2. 프로필 (Distill profile — 학습 설정)
// ============================================================================

function ProfilesTab() {
  const toast = useToast();
  const { data, isLoading, isError } = useDistillProfiles();
  const del = useDeleteDistillProfile();
  const profiles = data ?? [];

  async function onDelete(p: DistillProfile) {
    if (!confirm(`프로필 '${p.name}' 삭제하시겠습니까?\n관련 build/training-data 도 영향받을 수 있습니다.`)) return;
    try {
      await del.mutateAsync(p.name);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  const columns: Column<DistillProfile>[] = [
    {
      key: "name",
      header: "프로필",
      render: (p) => (
        <div className="flex items-center gap-2">
          <span className="font-medium text-fg-default">{p.name}</span>
          {p.enabled ? <Badge tone="success">활성</Badge> : <Badge tone="neutral">비활성</Badge>}
        </div>
      ),
    },
    {
      key: "search_group",
      header: "검색 그룹",
      render: (p) => <span className="text-fg-muted">{p.search_group ?? "—"}</span>,
    },
    {
      key: "base_model",
      header: "베이스 모델",
      render: (p) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {p.base_model ?? "—"}
        </span>
      ),
    },
    {
      key: "training",
      header: "학습 (epoch / batch / lr)",
      render: (p) =>
        p.training ? (
          <span className="font-mono text-[10px] text-fg-muted">
            {p.training.epochs}ep · bs={p.training.batch_size} · lr={p.training.learning_rate}
          </span>
        ) : "—",
    },
    {
      key: "deploy",
      header: "양자화",
      render: (p) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {p.deploy?.quantize ?? "—"}
        </span>
      ),
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (p) => (
        <Button size="sm" variant="ghost" onClick={() => onDelete(p)}>
          삭제
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <p className="text-xs text-fg-muted">
        프로필 신규 생성/수정은 큰 JSON 폼이 필요해 후속 작업으로 분리됨.
        지금은 list / 삭제만.
      </p>
      {isLoading ? <Skeleton className="h-48" /> : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          프로필 목록을 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<DistillProfile> columns={columns} rows={profiles} rowKey={(r) => r.name} empty="등록된 프로필이 없습니다" />
      )}
    </div>
  );
}

// ============================================================================
// 3. 베이스 모델 (registry CRUD)
// ============================================================================

function BaseModelsTab() {
  const toast = useToast();
  const { data, isLoading, isError } = useBaseModels();
  const upsert = useUpsertBaseModel();
  const del = useDeleteBaseModel();
  const [editing, setEditing] = useState<BaseModel | null>(null);
  const [creating, setCreating] = useState(false);
  const models = data ?? [];

  async function onDelete(m: BaseModel) {
    if (!confirm(`베이스 모델 '${m.hf_id}' 삭제하시겠습니까?`)) return;
    try {
      await del.mutateAsync(m.hf_id);
      toast.push("삭제되었습니다", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  const columns: Column<BaseModel>[] = [
    {
      key: "display_name",
      header: "이름",
      render: (m) => (
        <div className="flex items-center gap-2">
          <span className="font-medium text-fg-default">{m.display_name}</span>
          {m.verified && <Badge tone="success">verified</Badge>}
          {m.commercial_use ? <Badge tone="accent">상업 OK</Badge> : <Badge tone="warning">조건부</Badge>}
        </div>
      ),
    },
    {
      key: "hf_id",
      header: "HF ID",
      render: (m) => (
        <span className="font-mono text-[10px] text-fg-muted">{m.hf_id}</span>
      ),
    },
    {
      key: "params",
      header: "파라미터",
      render: (m) => <span className="text-fg-muted">{m.params ?? "—"}</span>,
    },
    {
      key: "license",
      header: "라이센스",
      render: (m) => <span className="text-fg-muted">{m.license ?? "—"}</span>,
    },
    {
      key: "enabled",
      header: "노출",
      render: (m) =>
        m.enabled ? <Badge tone="success">enabled</Badge> : <Badge tone="neutral">hidden</Badge>,
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (m) => (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" onClick={() => setEditing(m)}>
            수정
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onDelete(m)}>
            삭제
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <p className="text-xs text-fg-muted">
          Distill 프로필이 base 로 선택할 수 있는 모델 레지스트리.
        </p>
        <Button size="sm" onClick={() => setCreating(true)}>
          + 신규 모델
        </Button>
      </div>
      {isLoading ? <Skeleton className="h-48" /> : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          베이스 모델 레지스트리를 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<BaseModel> columns={columns} rows={models} rowKey={(r) => r.hf_id} empty="등록된 모델이 없습니다" />
      )}

      <BaseModelFormDialog
        key={editing?.hf_id ?? (creating ? "create" : "closed")}
        open={creating || editing !== null}
        initial={editing}
        onClose={() => { setCreating(false); setEditing(null); }}
        onSubmit={async (body) => {
          try {
            await upsert.mutateAsync(body);
            toast.push(editing ? "수정되었습니다" : "등록되었습니다", "success");
            setCreating(false);
            setEditing(null);
          } catch (e) {
            toast.push(e instanceof Error ? e.message : "저장 실패", "danger");
          }
        }}
        pending={upsert.isPending}
      />
    </div>
  );
}

function BaseModelFormDialog({
  open, initial, onClose, onSubmit, pending,
}: {
  open: boolean;
  initial: BaseModel | null;
  onClose: () => void;
  onSubmit: (body: BaseModel) => void;
  pending: boolean;
}) {
  const [hfId, setHfId] = useState(initial?.hf_id ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [params, setParams] = useState(initial?.params ?? "");
  const [license, setLicense] = useState(initial?.license ?? "");
  const [commercialUse, setCommercialUse] = useState(initial?.commercial_use ?? false);
  const [verified, setVerified] = useState(initial?.verified ?? false);
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [sortOrder, setSortOrder] = useState(initial?.sort_order ?? 0);

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      hf_id: hfId.trim(), display_name: displayName.trim(),
      params: params.trim() || undefined,
      license: license.trim() || undefined,
      commercial_use: commercialUse, verified,
      notes: notes.trim(), enabled, sort_order: sortOrder,
    });
  }

  return (
    <Dialog
      open={open} onClose={onClose}
      title={initial ? `베이스 모델 수정 — ${initial.hf_id}` : "신규 베이스 모델"}
      width="lg"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>취소</Button>
          <Button type="submit" form="bm-form" disabled={pending || !hfId.trim() || !displayName.trim()}>
            {pending ? "저장 중…" : "저장"}
          </Button>
        </>
      }
    >
      <form id="bm-form" onSubmit={submit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            Hugging Face ID
            <Input value={hfId} onChange={(e) => setHfId(e.target.value)} required disabled={Boolean(initial)} autoFocus />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            표시 이름
            <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            파라미터 (예: 3B, 7B)
            <Input value={params} onChange={(e) => setParams(e.target.value)} />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            라이센스
            <Input value={license} onChange={(e) => setLicense(e.target.value)} />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            정렬 순서
            <Input type="number" value={sortOrder} onChange={(e) => setSortOrder(Number(e.target.value))} />
          </label>
          <div className="flex flex-col gap-2 pt-5">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={commercialUse} onChange={(e) => setCommercialUse(e.target.checked)} />
              상업 사용 OK
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
              검증됨
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              드롭다운 노출
            </label>
          </div>
        </div>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          비고
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
        </label>
      </form>
    </Dialog>
  );
}

// ============================================================================
// 4. 학습/빌드
// ============================================================================

function BuildsTab() {
  const toast = useToast();
  const { data, isLoading, isError } = useDistillBuilds();
  const profiles = useDistillProfiles();
  const retrain = useTriggerRetrain();
  const deploy = useDeployBuild();
  const rollback = useRollbackBuild();
  const del = useDeleteBuild();
  const [retrainProfile, setRetrainProfile] = useState<string>("");

  const builds = data ?? [];

  async function onRetrain() {
    if (!retrainProfile) return;
    try {
      const res = await retrain.mutateAsync(retrainProfile);
      toast.push(`'${retrainProfile}' 재학습 시작 (${res.build_id?.slice(0, 8) ?? "?"}…)`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "재학습 실패", "danger");
    }
  }

  async function onDeploy(b: DistillBuild) {
    try {
      await deploy.mutateAsync(b.id);
      toast.push(`'${b.profile_name}' ${b.version} 배포됨`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "배포 실패", "danger");
    }
  }

  async function onRollback(b: DistillBuild) {
    if (!confirm(`'${b.profile_name}' ${b.version} 롤백?`)) return;
    try {
      await rollback.mutateAsync(b.id);
      toast.push("롤백됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "롤백 실패", "danger");
    }
  }

  async function onDelete(b: DistillBuild) {
    if (!confirm(`Build ${b.version} 삭제?`)) return;
    try {
      await del.mutateAsync(b.id);
      toast.push("삭제됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "삭제 실패", "danger");
    }
  }

  const columns: Column<DistillBuild>[] = [
    {
      key: "version",
      header: "버전",
      render: (b) => (
        <span className="font-mono text-[10px] text-fg-default">{b.version ?? "—"}</span>
      ),
    },
    {
      key: "profile_name",
      header: "프로필",
      render: (b) => <span className="text-fg-muted">{b.profile_name ?? "—"}</span>,
    },
    {
      key: "status",
      header: "상태",
      render: (b) => (
        <SeverityBadge level={statusToSeverity(b.status)}>
          {b.status ?? "—"}
        </SeverityBadge>
      ),
    },
    {
      key: "training_samples",
      header: "샘플",
      align: "right",
      render: (b) => (b.training_samples ?? 0).toLocaleString(),
    },
    {
      key: "train_loss",
      header: "Loss",
      align: "right",
      render: (b) => b.train_loss?.toFixed(4) ?? "—",
    },
    {
      key: "training_duration_sec",
      header: "학습시간",
      align: "right",
      render: (b) =>
        b.training_duration_sec ? `${Math.round(b.training_duration_sec / 60)}분` : "—",
    },
    {
      key: "_actions",
      header: "",
      align: "right",
      render: (b) => (
        <div className="flex justify-end gap-1">
          {b.status === "completed" && (
            <Button size="sm" variant="ghost" onClick={() => onDeploy(b)}>배포</Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => onRollback(b)}>롤백</Button>
          <Button size="sm" variant="ghost" onClick={() => onDelete(b)}>삭제</Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-end gap-3">
        <label className="block flex-1 space-y-1 text-xs font-medium text-fg-muted">
          재학습할 프로필
          <Select value={retrainProfile} onChange={(e) => setRetrainProfile(e.target.value)}>
            <option value="">— 프로필 선택 —</option>
            {(profiles.data ?? []).map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </Select>
        </label>
        <Button onClick={onRetrain} disabled={!retrainProfile || retrain.isPending}>
          {retrain.isPending ? "시작 중…" : "재학습 실행"}
        </Button>
      </div>
      {isLoading ? <Skeleton className="h-48" /> : isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          빌드 목록을 불러올 수 없습니다
        </div>
      ) : (
        <DataTable<DistillBuild> columns={columns} rows={builds} rowKey={(r) => r.id} empty="아직 빌드 기록이 없습니다" />
      )}
    </div>
  );
}

// ============================================================================
// 5. 데이터 큐레이션
// ============================================================================

function TrainingDataTab() {
  const toast = useToast();
  const profiles = useDistillProfiles();
  const [profileName, setProfileName] = useState("");
  const stats = useTrainingDataStats(profileName || null);
  const generate = useTriggerGenerateTrainingData();
  const [numSamples, setNumSamples] = useState(100);

  async function onGenerate() {
    if (!profileName) return;
    try {
      const res = await generate.mutateAsync({ profile_name: profileName, num_samples: numSamples });
      toast.push(`생성 시작 (batch ${res.batch_id?.slice(0, 8) ?? "?"}…)`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "생성 실패", "danger");
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4 sm:col-span-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            프로필 선택
            <Select value={profileName} onChange={(e) => setProfileName(e.target.value)}>
              <option value="">— 프로필 선택 —</option>
              {(profiles.data ?? []).map((p) => (
                <option key={p.name} value={p.name}>{p.name}</option>
              ))}
            </Select>
          </label>
        </div>
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            생성할 샘플 수
            <Input
              type="number"
              value={numSamples}
              onChange={(e) => setNumSamples(Number(e.target.value))}
              min={10}
              max={5000}
            />
          </label>
        </div>
      </div>

      {!profileName ? (
        <div className="rounded-lg border border-dashed border-border-default bg-bg-subtle px-6 py-10 text-center text-sm text-fg-muted">
          프로필을 선택하면 통계가 표시됩니다.
        </div>
      ) : stats.isLoading ? (
        <Skeleton className="h-32" />
      ) : stats.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm text-danger-default">
          통계를 불러올 수 없습니다
        </div>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard label="총 샘플" value={stats.data?.total ?? 0} />
            <MetricCard label="승인" value={stats.data?.approved ?? 0} tone="success" />
            <MetricCard label="대기" value={stats.data?.pending ?? 0} tone={(stats.data?.pending ?? 0) > 0 ? "warning" : "neutral"} />
            <MetricCard label="거부" value={stats.data?.rejected ?? 0} tone={(stats.data?.rejected ?? 0) > 0 ? "danger" : "neutral"} />
          </div>
          {Object.keys(stats.data?.by_source ?? {}).length > 0 && (
            <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
              <h3 className="mb-3 text-sm font-medium text-fg-default">소스별 분포</h3>
              <ul className="space-y-1 text-xs">
                {Object.entries(stats.data?.by_source ?? {}).map(([src, n]) => (
                  <li key={src} className="flex justify-between">
                    <span className="text-fg-muted">{src}</span>
                    <span className="tabular-nums text-fg-default">{n.toLocaleString()}</span>
                  </li>
                ))}
              </ul>
            </article>
          )}
          <Button onClick={onGenerate} disabled={generate.isPending}>
            {generate.isPending ? "생성 중…" : `+${numSamples} QA 생성`}
          </Button>
        </>
      )}
    </div>
  );
}

// ============================================================================
// 6. 운영/배포 (manifest)
// ============================================================================

function OperationsTab() {
  const profiles = useDistillProfiles();
  const [profileName, setProfileName] = useState("");
  const manifest = useEdgeManifest(profileName || null);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          프로필 선택
          <Select value={profileName} onChange={(e) => setProfileName(e.target.value)}>
            <option value="">— 프로필 선택 —</option>
            {(profiles.data ?? []).map((p) => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </Select>
        </label>
      </div>

      {!profileName ? (
        <div className="rounded-lg border border-dashed border-border-default bg-bg-subtle px-6 py-10 text-center text-sm text-fg-muted">
          프로필을 선택하면 현재 manifest 가 표시됩니다.
        </div>
      ) : manifest.isLoading ? (
        <Skeleton className="h-32" />
      ) : manifest.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            Manifest 를 불러올 수 없습니다
          </div>
          <p className="text-xs text-fg-muted">
            아직 배포된 build 가 없거나 S3 manifest 파일 누락
          </p>
        </div>
      ) : (
        <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
          <h2 className="mb-3 text-sm font-medium text-fg-default">
            현재 배포 manifest — {profileName}
          </h2>
          <dl className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <dt className="text-fg-muted">버전</dt>
              <dd className="mt-0.5 font-mono text-fg-default">
                {manifest.data?.version ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">App 버전</dt>
              <dd className="mt-0.5 font-mono text-fg-default">
                {manifest.data?.app_version ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">베이스 모델</dt>
              <dd className="mt-0.5 font-mono text-fg-default">
                {manifest.data?.base_model ?? "—"}
              </dd>
            </div>
            <div>
              <dt className="text-fg-muted">SHA256</dt>
              <dd className="mt-0.5 break-all font-mono text-[10px] text-fg-subtle">
                {manifest.data?.model_sha256 ?? "—"}
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-fg-muted">Model URL</dt>
              <dd className="mt-0.5 break-all font-mono text-[10px] text-fg-subtle">
                {manifest.data?.model_url ?? "—"}
              </dd>
            </div>
          </dl>
        </article>
      )}
    </div>
  );
}
