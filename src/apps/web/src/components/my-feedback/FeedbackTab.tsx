"use client";

import { useState, type FormEvent } from "react";

import {
  Badge,
  Button,
  EmptyState,
  ErrorFallback,
  Input,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { useMyFeedbackList, useSubmitFeedback } from "@/hooks/useFeedback";
import type { FeedbackBody, FeedbackItem } from "@/lib/api/endpoints";

const TYPES: { id: FeedbackBody["feedback_type"]; label: string; tone: "success" | "danger" | "accent" | "warning" | "neutral" }[] = [
  // emoji 제거 — <option> 안에서는 lucide React 컴포넌트 렌더 불가 (browser
  // native rendering). 시각 구분은 Badge tone (success/danger/accent/warning)
  // 으로 충분.
  { id: "UPVOTE", label: "좋아요", tone: "success" },
  { id: "DOWNVOTE", label: "싫어요", tone: "danger" },
  { id: "CORRECTION", label: "수정 제안", tone: "accent" },
  { id: "ERROR_REPORT", label: "오류 신고", tone: "warning" },
  { id: "SUGGESTION", label: "개선 제안", tone: "accent" },
];

export function FeedbackTab() {
  const toast = useToast();
  const [feedbackType, setFeedbackType] =
    useState<FeedbackBody["feedback_type"]>("SUGGESTION");
  const [documentId, setDocumentId] = useState("");
  const [content, setContent] = useState("");

  const submit = useSubmitFeedback();
  const list = useMyFeedbackList({ page: 1, page_size: 20 });

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!content.trim()) return;
    try {
      await submit.mutateAsync({
        feedback_type: feedbackType,
        document_id: documentId.trim() || null,
        content,
      });
      toast.push("피드백이 제출되었습니다.", "success");
      setContent("");
      setDocumentId("");
    } catch {
      toast.push("제출에 실패했습니다.", "danger");
    }
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={onSubmit}
        className="space-y-3 rounded-lg border border-border-default bg-bg-canvas p-4 shadow-sm"
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            유형
            <Select
              value={feedbackType}
              onChange={(e) =>
                setFeedbackType(e.target.value as FeedbackBody["feedback_type"])
              }
            >
              {TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </Select>
          </label>
          <label className="block space-y-1 text-xs font-medium text-fg-muted">
            관련 문서 ID (선택)
            <Input
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value)}
              placeholder="document_id"
            />
          </label>
        </div>

        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          내용
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            maxLength={1000}
            placeholder="구체적으로 알려주세요. (최대 1,000자)"
            required
          />
        </label>

        <div className="flex justify-end">
          <Button type="submit" disabled={submit.isPending || !content.trim()}>
            {submit.isPending ? "제출 중…" : "제출"}
          </Button>
        </div>
      </form>

      <div>
        <h2 className="mb-3 text-sm font-medium text-fg-default">최근 피드백</h2>
        {list.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, idx) => (
              <Skeleton key={idx} className="h-16" />
            ))}
          </div>
        ) : list.isError ? (
          <ErrorFallback
            title="피드백 목록을 불러올 수 없습니다"
            error={list.error}
            onRetry={() => list.refetch()}
          />
        ) : (list.data?.items ?? []).length === 0 ? (
          <EmptyState
            title="아직 제출한 피드백이 없습니다"
            description="위 폼에서 첫 피드백을 남겨 보세요."
          />
        ) : (
          <ul className="space-y-2">
            {(list.data?.items ?? []).map((item: FeedbackItem, idx) => {
              const type = item.feedback_type ?? "";
              const status = item.status ?? "pending";
              const created = item.created_at ?? "";
              const txt = item.content ?? "";
              const matched = TYPES.find((t) => t.id === type);
              const tone = matched?.tone ?? "neutral";
              return (
                <li
                  key={item.id ?? idx}
                  className="rounded-md border border-border-default bg-bg-canvas px-4 py-3 text-sm"
                >
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <Badge tone={tone}>{matched?.label ?? type ?? "(유형 없음)"}</Badge>
                    <span className="text-fg-muted">{status}</span>
                    <span className="ml-auto font-mono text-fg-subtle">
                      {created.slice(0, 19).replace("T", " ")}
                    </span>
                  </div>
                  <p className="text-fg-default">{txt}</p>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
