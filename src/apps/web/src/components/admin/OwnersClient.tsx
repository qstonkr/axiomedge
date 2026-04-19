"use client";

import { useState } from "react";

import { Select, Skeleton } from "@/components/ui";
import { useDocumentOwners } from "@/hooks/admin/useContent";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { DocumentOwner } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";
import { MetricCard } from "./MetricCard";

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
}

export function OwnersClient() {
  const { data: kbs } = useSearchableKbs();
  const [kbId, setKbId] = useState<string>("");
  const owners = useDocumentOwners(kbId || null);

  const items = owners.data ?? [];
  // owner_user_id 별로 카운트 (한 owner 가 여러 문서 담당)
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
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-fg-default">담당자 관리</h1>
        <p className="text-sm text-fg-muted">
          KB 별 문서 담당자 — 자동 추출 + 수동 지정. KB 를 선택해 목록 조회.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-border-default bg-bg-canvas p-4 sm:col-span-1">
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
        <MetricCard label="등록 문서" value={items.length} />
        <MetricCard label="고유 담당자" value={byUser.size} />
      </div>

      {!kbId ? (
        <div className="rounded-lg border border-dashed border-border-default bg-bg-subtle px-6 py-10 text-center text-sm text-fg-muted">
          KB 를 선택하면 해당 KB 의 담당자 목록이 표시됩니다.
        </div>
      ) : owners.isLoading ? (
        <Skeleton className="h-48" />
      ) : owners.isError ? (
        <div className="rounded-lg border border-danger-default/30 bg-danger-subtle p-4 text-sm">
          <div className="mb-2 font-medium text-danger-default">
            담당자 목록을 불러올 수 없습니다
          </div>
          <p className="font-mono text-xs text-fg-muted">
            {(owners.error as Error)?.message ?? "알 수 없는 오류"}
          </p>
        </div>
      ) : (
        <DataTable<DocumentOwner>
          columns={columns}
          rows={items}
          rowKey={(r) => r.id}
          empty="이 KB 의 담당자가 없습니다"
        />
      )}
    </section>
  );
}
