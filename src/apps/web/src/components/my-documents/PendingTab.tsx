"use client";

import { CheckCircle2 } from "lucide-react";

import { Badge, EmptyState, Skeleton } from "@/components/ui";
import { usePendingTasks } from "@/hooks/useMyDocuments";

function fmtDate(s: unknown): string {
  return typeof s === "string" ? s.slice(0, 19).replace("T", " ") : "";
}

export function PendingTab() {
  const { verifications, feedback, errors, isLoading } = usePendingTasks();

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, idx) => (
          <Skeleton key={idx} className="h-16" />
        ))}
      </div>
    );
  }

  const total = verifications.length + feedback.length + errors.length;
  if (total === 0) {
    return (
      <EmptyState
        icon={<CheckCircle2 size={32} strokeWidth={1.5} className="text-success-default" />}
        title="대기 작업이 없습니다"
        description="처리할 검증/피드백/오류 신고가 없습니다."
      />
    );
  }

  return (
    <div className="space-y-6">
      <Section
        title="검증 대기"
        items={verifications.map((it) => {
          const obj = it as Record<string, unknown>;
          return {
            primary: String(obj.title ?? obj.document_id ?? "—"),
            secondary: fmtDate(obj.created_at),
            tone: "accent" as const,
            tag: String(obj.type ?? "verification"),
          };
        })}
      />
      <Section
        title="피드백 대기"
        items={feedback.map((it) => {
          const obj = it as Record<string, unknown>;
          return {
            primary: String(obj.content ?? "(내용 없음)"),
            secondary: fmtDate(obj.created_at),
            tone: "warning" as const,
            tag: String(obj.feedback_type ?? "feedback"),
          };
        })}
      />
      <Section
        title="오류 신고 대기"
        items={errors.map((it) => {
          const obj = it as Record<string, unknown>;
          return {
            primary: String(obj.title ?? "(제목 없음)"),
            secondary: fmtDate(obj.created_at),
            tone: "danger" as const,
            tag: String(obj.priority ?? "—"),
          };
        })}
      />
    </div>
  );
}

function Section({
  title,
  items,
}: {
  title: string;
  items: { primary: string; secondary: string; tone: "accent" | "warning" | "danger"; tag: string }[];
}) {
  if (items.length === 0) return null;
  return (
    <section>
      <h2 className="mb-2 text-sm font-medium text-fg-default">
        {title}{" "}
        <span className="font-normal text-fg-muted">({items.length})</span>
      </h2>
      <ul className="space-y-2">
        {items.slice(0, 10).map((it, idx) => (
          <li
            key={idx}
            className="flex items-start gap-3 rounded-md border border-border-default bg-bg-canvas px-4 py-3 text-sm"
          >
            <Badge tone={it.tone}>{it.tag}</Badge>
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-fg-default">{it.primary}</p>
              {it.secondary && (
                <p className="mt-1 font-mono text-xs text-fg-subtle">
                  {it.secondary}
                </p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
