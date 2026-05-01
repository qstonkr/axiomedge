"use client";

import { useState } from "react";
import { FileText } from "lucide-react";

import {
  Badge,
  EmptyState,
  Select,
  Skeleton,
} from "@/components/ui";
import { useSearchableKbs } from "@/hooks/useSearch";
import { useMyDocumentOwners } from "@/hooks/useMyDocuments";

export function MyDocumentsTab({ userId }: { userId: string }) {
  const [kbId, setKbId] = useState<string | undefined>(undefined);
  const { data: kbs } = useSearchableKbs();
  const { data, isLoading } = useMyDocumentOwners({ kb_id: kbId, userId });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="block w-64 space-y-1 text-xs font-medium text-fg-muted">
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
        <span className="text-xs text-fg-muted">
          담당 문서 {data?.length ?? 0}개
        </span>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, idx) => (
            <Skeleton key={idx} className="h-12" />
          ))}
        </div>
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<FileText size={32} strokeWidth={1.5} />}
          title="담당 문서가 없습니다"
          description={
            kbId
              ? "다른 KB 를 선택하거나 필터를 해제해 보세요."
              : "관리자가 담당자를 지정하면 여기에 표시됩니다."
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border-default">
          <table className="min-w-full divide-y divide-border-default text-sm">
            <thead className="bg-bg-subtle text-xs text-fg-muted">
              <tr>
                <th className="px-3 py-2 text-left font-medium">제목</th>
                <th className="px-3 py-2 text-left font-medium">KB</th>
                <th className="px-3 py-2 text-left font-medium">유형</th>
                <th className="px-3 py-2 text-left font-medium">상태</th>
                <th className="px-3 py-2 text-left font-medium">마지막 검증</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-default bg-bg-canvas">
              {(data ?? []).map((row) => (
                <tr key={row.id}>
                  <td className="px-3 py-2 text-fg-default">
                    {row.document_title ?? row.document_id}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-fg-muted">
                    {row.kb_id ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-fg-muted">
                    {row.ownership_type ?? "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge tone={row.status === "active" ? "success" : "neutral"}>
                      {row.status ?? "—"}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-fg-subtle">
                    {row.last_verified_at?.slice(0, 10) ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
