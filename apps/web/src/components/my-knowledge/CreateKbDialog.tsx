"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  Input,
  Textarea,
  useToast,
} from "@/components/ui";
import { useCreatePersonalKb } from "@/hooks/useMyKnowledge";

/** Slugify Korean/Latin user input into a kb_id-safe string. */
function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .trim()
      .replace(/\s+/g, "-")
      .replace(/[^a-z0-9가-힣\-]/g, "")
      .slice(0, 60) || "kb"
  );
}

export function CreateKbDialog({
  userId,
  onClose,
}: {
  userId: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const create = useCreatePersonalKb(userId);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const kbId =
    `pkb_${userId.replace(/-/g, "").slice(0, 12)}_${slugify(name).slice(0, 30)}`;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      await create.mutateAsync({ kb_id: kbId, name: name.trim(), description });
      toast.push("새 personal KB 가 생성되었습니다.", "success");
      onClose();
    } catch (err) {
      const detail = err instanceof Error ? err.message : "생성에 실패했습니다.";
      toast.push(detail, "danger");
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="새 KB 만들기"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 px-4 py-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md space-y-4 rounded-lg border border-border-default bg-bg-canvas p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-medium text-fg-default">새 KB 만들기</h2>
        <form onSubmit={onSubmit} className="space-y-3">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            이름
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="예: 사이드 프로젝트 메모"
              autoFocus
              required
              maxLength={200}
            />
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            설명 (선택)
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="이 KB 의 용도를 적어두면 나중에 찾기 쉽습니다."
              maxLength={500}
            />
          </label>
          <p className="text-xs text-fg-subtle">
            kb_id (자동 생성):
            <span className="ml-2 font-mono text-fg-muted">{kbId}</span>
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              취소
            </Button>
            <Button
              type="submit"
              disabled={create.isPending || !name.trim()}
            >
              {create.isPending ? "생성 중…" : "생성"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
