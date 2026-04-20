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
import { useMyErrorReportsList, useSubmitErrorReport } from "@/hooks/useFeedback";
import type { ErrorReportBody, ErrorReportItem } from "@/lib/api/endpoints";

const ERROR_TYPES: { id: ErrorReportBody["error_type"]; label: string }[] = [
  { id: "INACCURATE", label: "부정확" },
  { id: "OUTDATED", label: "오래됨" },
  { id: "INCOMPLETE", label: "누락" },
  { id: "DUPLICATE", label: "중복" },
  { id: "BROKEN_LINK", label: "끊긴 링크" },
  { id: "FORMATTING", label: "포맷 오류" },
  { id: "OTHER", label: "기타" },
];

const PRIORITIES: { id: ErrorReportBody["priority"]; label: string; tone: "danger" | "warning" | "neutral" | "accent" }[] = [
  { id: "CRITICAL", label: "긴급", tone: "danger" },
  { id: "HIGH", label: "높음", tone: "warning" },
  { id: "MEDIUM", label: "보통", tone: "neutral" },
  { id: "LOW", label: "낮음", tone: "accent" },
];

export function ErrorReportTab() {
  const toast = useToast();
  const [errorType, setErrorType] =
    useState<ErrorReportBody["error_type"]>("INACCURATE");
  const [priority, setPriority] =
    useState<ErrorReportBody["priority"]>("MEDIUM");
  const [title, setTitle] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [description, setDescription] = useState("");

  const submit = useSubmitErrorReport();
  const list = useMyErrorReportsList({ page: 1, page_size: 20 });

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim() || !description.trim()) return;
    try {
      await submit.mutateAsync({
        error_type: errorType,
        priority,
        title,
        description,
        document_id: documentId.trim() || null,
      });
      toast.push("오류 신고가 접수되었습니다.", "success");
      setTitle("");
      setDescription("");
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
          제목
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="간단한 요약"
            required
          />
        </label>

        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          관련 문서 ID (선택)
          <Input
            value={documentId}
            onChange={(e) => setDocumentId(e.target.value)}
            placeholder="document_id"
          />
        </label>

        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          설명
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={5000}
            placeholder="어떤 점이 잘못되었는지 자세히 알려주세요. (최대 5,000자)"
            required
          />
        </label>

        <div className="flex justify-end">
          <Button
            type="submit"
            disabled={submit.isPending || !title.trim() || !description.trim()}
          >
            {submit.isPending ? "제출 중…" : "제출"}
          </Button>
        </div>
      </form>

      <div>
        <h2 className="mb-3 text-sm font-medium text-fg-default">최근 오류 신고</h2>
        {list.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, idx) => (
              <Skeleton key={idx} className="h-20" />
            ))}
          </div>
        ) : list.isError ? (
          <ErrorFallback
            title="오류 신고 목록을 불러올 수 없습니다"
            error={list.error}
            onRetry={() => list.refetch()}
          />
        ) : (list.data?.items ?? []).length === 0 ? (
          <EmptyState
            title="아직 제출한 오류 신고가 없습니다"
            description="위 폼에서 첫 신고를 남겨 보세요."
          />
        ) : (
          <ul className="space-y-2">
            {(list.data?.items ?? []).map((item: ErrorReportItem, idx) => {
              const t = item.title ?? "(제목 없음)";
              const status = item.status ?? "pending";
              const pType = item.error_type ?? "";
              const pri = item.priority ?? "MEDIUM";
              const matchedPri = PRIORITIES.find((p) => p.id === pri);
              const matchedType = ERROR_TYPES.find((x) => x.id === pType);
              const tone = matchedPri?.tone ?? "neutral";
              const created = item.created_at ?? "";
              return (
                <li
                  key={item.id ?? idx}
                  className="rounded-md border border-border-default bg-bg-canvas px-4 py-3 text-sm"
                >
                  <div className="mb-1 flex items-center gap-2 text-xs">
                    <Badge tone={tone}>{matchedPri?.label ?? pri}</Badge>
                    <span className="text-fg-muted">{matchedType?.label ?? pType}</span>
                    <span className="text-fg-muted">· {status}</span>
                    <span className="ml-auto font-mono text-fg-subtle">
                      {created.slice(0, 19).replace("T", " ")}
                    </span>
                  </div>
                  <p className="font-medium text-fg-default">{t}</p>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
