"use client";

import { useState } from "react";

import { Badge, Button, Card, useToast, cn } from "@/components/ui";
import { useDeleteKb } from "@/hooks/useMyKnowledge";
import type { Kb } from "@/lib/api/endpoints";

export function KbCard({
  kb,
  selected,
  onSelect,
  userId,
}: {
  kb: Kb;
  selected: boolean;
  onSelect: () => void;
  userId: string;
}) {
  const toast = useToast();
  const del = useDeleteKb(userId);
  const [confirming, setConfirming] = useState(false);

  async function onDelete() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    try {
      await del.mutateAsync(kb.kb_id);
      toast.push("삭제되었습니다.", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "삭제에 실패했습니다.";
      toast.push(detail, "danger");
    } finally {
      setConfirming(false);
    }
  }

  return (
    <Card
      hoverable
      padding="compact"
      className={cn(
        "cursor-pointer transition-all",
        selected && "ring-2 ring-accent-default",
      )}
      onClick={onSelect}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-medium text-fg-default">
            {kb.name}
          </h3>
          <p className="mt-1 font-mono text-xs text-fg-subtle">{kb.kb_id}</p>
        </div>
        <Badge tone="warning">개인</Badge>
      </div>
      {kb.description && (
        <p className="mt-2 line-clamp-2 text-xs text-fg-muted">
          {kb.description}
        </p>
      )}
      <div className="mt-3 flex items-center gap-3 text-xs text-fg-muted">
        <span>문서 {kb.document_count ?? 0}</span>
        <span>·</span>
        <span>chunk {kb.chunk_count ?? 0}</span>
      </div>
      <div className="mt-3 flex justify-end" onClick={(e) => e.stopPropagation()}>
        <Button
          size="sm"
          variant={confirming ? "danger" : "ghost"}
          onClick={onDelete}
          disabled={del.isPending}
        >
          {del.isPending ? "삭제 중…" : confirming ? "정말 삭제?" : "삭제"}
        </Button>
      </div>
    </Card>
  );
}
