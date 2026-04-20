"use client";

import { useState } from "react";

import {
  ErrorFallback,
  Select,
  Skeleton,
  Tabs,
} from "@/components/ui";
import { useDocumentOwners } from "@/hooks/admin/useContent";
import { useTopicOwners } from "@/hooks/admin/useOps";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { DocumentOwner, TopicOwner } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function OwnersClient() {
  const [kbId, setKbId] = useState<string>("");
  const { data: kbs } = useSearchableKbs();

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">담당자 관리</h1>
        <p className="text-sm text-fg-muted">
          KB 별 문서 담당자 + topic / 도메인 전문가 (SME). 두 layer 가
          상호 보완 — 문서 owner 는 자동 추출, topic owner 는 admin curated.
        </p>
      </header>

      <div className="rounded-lg border border-border-default bg-bg-canvas p-4">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          KB 선택
          <Select value={kbId} onChange={(e) => setKbId(e.target.value)}>
            <option value="">— KB 선택 —</option>
            {(kbs ?? []).map((kb) => (
              <option key={kb.kb_id} value={kb.kb_id}>
                {kb.name} ({kb.kb_id})
              </option>
            ))}
          </Select>
        </label>
      </div>

      {!kbId ? (
        <div className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-8 text-center text-xs text-fg-muted">
          KB 를 선택하면 담당자 / 전문가 정보가 표시됩니다.
        </div>
      ) : (
        <Tabs
          items={[
            {
              id: "documents",
              label: "문서 owner",
              content: <DocumentOwnersPanel kbId={kbId} />,
            },
            {
              id: "topics",
              label: "topic 전문가 (SME)",
              content: <TopicOwnersPanel kbId={kbId} />,
            },
          ]}
        />
      )}
    </section>
  );
}

function DocumentOwnersPanel({ kbId }: { kbId: string }) {
  const owners = useDocumentOwners(kbId);
  const items = owners.data ?? [];

  const byUser = new Map<string, number>();
  items.forEach((o: DocumentOwner) => {
    if (!o.owner_user_id) return;
    byUser.set(o.owner_user_id, (byUser.get(o.owner_user_id) ?? 0) + 1);
  });

  const columns: Column<DocumentOwner>[] = [
    {
      key: "owner_user_id",
      header: "담당자",
      render: (o) => (
        <span className="font-medium text-fg-default">{o.owner_user_id}</span>
      ),
    },
    {
      key: "document_id",
      header: "문서 ID",
      render: (o) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {o.document_id}
        </span>
      ),
    },
    {
      key: "document_title",
      header: "문서 제목",
      render: (o) => (
        <span className="line-clamp-1 text-fg-default">
          {o.document_title || "—"}
        </span>
      ),
    },
    {
      key: "ownership_type",
      header: "타입",
      render: (o) => (
        <span className="text-fg-muted">{o.ownership_type || "—"}</span>
      ),
    },
    {
      key: "assigned_at",
      header: "할당 시각",
      render: (o) => (
        <span className="font-mono text-[10px] text-fg-muted">
          {fmtDate(o.assigned_at)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="등록 문서" value={items.length} />
        <MetricCard label="고유 담당자" value={byUser.size} />
      </div>
      {owners.isLoading ? (
        <Skeleton className="h-48" />
      ) : owners.isError ? (
        <ErrorFallback
          title="담당자 목록을 불러올 수 없습니다"
          error={owners.error}
          onRetry={() => owners.refetch()}
        />
      ) : (
        <DataTable<DocumentOwner>
          columns={columns}
          rows={items}
          rowKey={(r) => r.id}
          empty="이 KB 의 담당자가 없습니다"
        />
      )}
    </div>
  );
}

function TopicOwnersPanel({ kbId }: { kbId: string }) {
  const topics = useTopicOwners(kbId);
  const items = topics.data?.topics ?? [];

  const columns: Column<TopicOwner>[] = [
    {
      key: "topic_name",
      header: "topic",
      render: (t) => (
        <span className="font-medium text-fg-default">{t.topic_name}</span>
      ),
    },
    {
      key: "owner_user_id",
      header: "담당자",
      render: (t) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg-default">
            {t.email ?? t.owner_user_id}
          </span>
          {t.display_name && (
            <span className="text-[10px] text-fg-subtle">{t.display_name}</span>
          )}
        </div>
      ),
    },
    {
      key: "expertise",
      header: "전문 분야",
      render: (t) => (
        <div className="flex flex-wrap gap-1">
          {(t.expertise ?? []).slice(0, 5).map((e) => (
            <span
              key={e}
              className="rounded-full bg-bg-muted px-2 py-0.5 text-[11px] text-fg-default"
            >
              {e}
            </span>
          ))}
          {(t.expertise?.length ?? 0) > 5 && (
            <span className="text-xs text-fg-subtle">
              +{(t.expertise?.length ?? 0) - 5}
            </span>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <p className="text-xs text-fg-muted">
        💡 Topic 전문가 (SME) 는 admin 이 직접 curate 하는 도메인 전문가
        매핑. 문서 owner 와 별개로 search/agentic 에서 우선 추천에 사용.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <MetricCard label="등록 topic" value={topics.data?.total ?? 0} />
        <MetricCard
          label="고유 전문가"
          value={
            new Set(items.map((t) => t.owner_user_id).filter(Boolean)).size
          }
        />
      </div>
      {topics.isLoading ? (
        <Skeleton className="h-32" />
      ) : topics.isError ? (
        <ErrorFallback
          title="topic 전문가 목록을 불러올 수 없습니다"
          error={topics.error}
          onRetry={() => topics.refetch()}
        />
      ) : (
        <DataTable<TopicOwner>
          columns={columns}
          rows={items}
          rowKey={(r) => `${r.topic_name}-${r.owner_user_id}`}
          empty="이 KB 에 등록된 topic 전문가가 없습니다."
        />
      )}
      <p className="text-[10px] text-fg-subtle">
        ※ 신규 topic 등록은 backend{" "}
        <code className="font-mono">POST /admin/ownership/topics</code> 로
        가능 — UI form 은 후속.
      </p>
    </div>
  );
}
