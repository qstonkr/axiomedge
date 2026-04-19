"use client";

import { useState, type FormEvent } from "react";

import {
  Button,
  Select,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  submitErrorReport,
  type ErrorReportBody,
} from "@/lib/api/endpoints";

import type { ChunkSource } from "./types";

const ERROR_TYPES: { id: ErrorReportBody["error_type"]; label: string }[] = [
  { id: "INACCURATE", label: "부정확" },
  { id: "OUTDATED", label: "오래됨" },
  { id: "INCOMPLETE", label: "누락" },
  { id: "DUPLICATE", label: "중복" },
  { id: "BROKEN_LINK", label: "끊긴 링크" },
  { id: "FORMATTING", label: "포맷 오류" },
  { id: "OTHER", label: "기타" },
];

const PRIORITIES: { id: ErrorReportBody["priority"]; label: string }[] = [
  { id: "MEDIUM", label: "보통" },
  { id: "HIGH", label: "높음" },
  { id: "LOW", label: "낮음" },
  { id: "CRITICAL", label: "긴급" },
];

export function ErrorReportDialog({
  chunk,
  onClose,
}: {
  chunk: ChunkSource;
  onClose: () => void;
}) {
  const toast = useToast();
  const [errorType, setErrorType] =
    useState<ErrorReportBody["error_type"]>("INACCURATE");
  const [priority, setPriority] =
    useState<ErrorReportBody["priority"]>("MEDIUM");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!description.trim()) return;
    setPending(true);
    try {
      await submitErrorReport({
        error_type: errorType,
        priority,
        title: `${chunk.document_name ?? chunk.id ?? "문서"} 오류 신고`,
        description,
        document_id: chunk.document_id ?? chunk.id ?? null,
      });
      toast.push("오류 신고가 접수되었습니다.", "success");
      onClose();
    } catch {
      toast.push("오류 신고 제출에 실패했습니다.", "danger");
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="오류 신고"
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 px-4 py-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg space-y-4 rounded-lg border border-border-default bg-bg-canvas p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="space-y-1">
          <h2 className="text-lg font-medium text-fg-default">오류 신고</h2>
          <p className="text-xs text-fg-muted">
            {chunk.document_name ?? chunk.kb_id ?? chunk.id}
          </p>
        </header>

        <form onSubmit={onSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-1 text-xs font-medium text-fg-muted">
              유형
              <Select
                value={errorType}
                onChange={(e) =>
                  setErrorType(e.target.value as ErrorReportBody["error_type"])
                }
              >
                {ERROR_TYPES.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </Select>
            </label>
            <label className="block space-y-1 text-xs font-medium text-fg-muted">
              우선순위
              <Select
                value={priority}
                onChange={(e) =>
                  setPriority(e.target.value as ErrorReportBody["priority"])
                }
              >
                {PRIORITIES.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </Select>
            </label>
          </div>

          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            설명
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="어떤 점이 잘못되었는지 구체적으로 알려주세요."
              required
              maxLength={5000}
            />
          </label>

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              취소
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? "제출 중…" : "제출"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
