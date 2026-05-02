"use client";

import { useState } from "react";
import { Bell } from "lucide-react";

import { EmptyState, Select, Skeleton } from "@/components/ui";
import { useSearchableKbs } from "@/hooks/useSearch";
import { useStaleOwners } from "@/hooks/useMyDocuments";

export function NotificationsTab() {
  const [kbId, setKbId] = useState<string | undefined>(undefined);
  const { data: kbs } = useSearchableKbs();
  const { data, isLoading } = useStaleOwners({ kb_id: kbId });

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-accent-subtle bg-accent-subtle px-4 py-3 text-sm text-accent-emphasis">
        <p>
          조직 변경 이벤트는 곧 여기에 표시됩니다 — 백엔드 OrgChangeEvent
          파이프라인 (B-2 이후) 와 연동 예정.
        </p>
      </section>

      <section className="space-y-3">
        <header className="flex items-end justify-between">
          <h2 className="text-sm font-medium text-fg-default">
            오래된 문서 (90일 이상 미검증)
          </h2>
          <label className="block w-48 space-y-1 text-xs font-medium text-fg-muted">
            KB 필터
            <Select
              value={kbId ?? ""}
              onChange={(e) => setKbId(e.target.value || undefined)}
            >
              <option value="">전체</option>
              {(kbs ?? []).map((kb) => (
                <option key={kb.kb_id} value={kb.kb_id}>
                  {kb.name}
                </option>
              ))}
            </Select>
          </label>
        </header>

        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, idx) => (
              <Skeleton key={idx} className="h-12" />
            ))}
          </div>
        ) : (data ?? []).length === 0 ? (
          <EmptyState
            icon={<Bell size={32} strokeWidth={1.5} />}
            title="오래된 문서가 없습니다"
            description="모든 담당 문서가 최근 검증되었습니다."
          />
        ) : (
          <ul className="space-y-2">
            {(data ?? []).slice(0, 50).map((row) => (
              <li
                key={row.id}
                className="flex items-center justify-between gap-3 rounded-md border border-warning-subtle bg-warning-subtle/40 px-4 py-2 text-sm"
              >
                <span className="line-clamp-1 flex-1 text-fg-default">
                  {row.document_title ?? row.document_id}
                </span>
                <span className="font-mono text-xs text-warning-default">
                  {row.last_verified_at?.slice(0, 10) ?? "검증 이력 없음"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
