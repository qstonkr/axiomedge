"use client";

import { useState, type ComponentType } from "react";
import {
  Activity,
  Database,
  FileText,
  KeyRound,
  MessageSquareWarning,
  Pin,
  Search,
} from "lucide-react";

import {
  ErrorFallback,
  Input,
  Select,
  Skeleton,
} from "@/components/ui";
import {
  useMyActivities,
  useMyActivitySummary,
} from "@/hooks/useMyActivities";
import type { MyActivity } from "@/lib/api/endpoints";

type IconComponent = ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean; className?: string }>;

const ACTIVITY_TYPES: { value: string; label: string; Icon: IconComponent }[] = [
  { value: "", label: "전체", Icon: Pin },
  { value: "search", label: "검색", Icon: Search },
  { value: "feedback", label: "피드백", Icon: MessageSquareWarning },
  { value: "document", label: "문서", Icon: FileText },
  { value: "login", label: "로그인", Icon: KeyRound },
  { value: "ingestion", label: "인제스트", Icon: Database },
];

function IconFor({ type }: { type: string | undefined }) {
  const Found = ACTIVITY_TYPES.find((t) => t.value === type)?.Icon ?? Pin;
  return <Found size={16} strokeWidth={1.75} aria-hidden className="text-fg-muted" />;
}

function fmtDate(s: unknown): string {
  if (typeof s !== "string") return "—";
  return s.slice(0, 19).replace("T", " ");
}

function detailString(d: unknown, m: unknown): string {
  const v = d ?? m;
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.slice(0, 200);
  try {
    return JSON.stringify(v).slice(0, 200);
  } catch {
    return String(v).slice(0, 200);
  }
}

function todayMinusDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function MyActivitiesPage() {
  const [activityType, setActivityType] = useState("");
  const [dateFrom, setDateFrom] = useState(() => todayMinusDays(30));
  const [dateTo, setDateTo] = useState(() => new Date().toISOString().slice(0, 10));

  const summary = useMyActivitySummary(30);
  const list = useMyActivities({
    activity_type: activityType || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    limit: 100,
  });

  const items: MyActivity[] = list.data?.activities ?? [];
  const total = list.data?.total ?? items.length;
  const sum = summary.data;

  return (
    <section className="mx-auto w-full max-w-4xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="flex items-center gap-2 text-2xl font-semibold leading-snug text-fg-default">
          <Activity aria-hidden size={22} strokeWidth={1.75} className="text-accent-default" />
          <span>나의 활동</span>
        </h1>
        <p className="text-sm text-fg-muted">
          최근 활동 요약 + 통합 timeline (검색 / 피드백 / 문서 / 로그인 등).
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-4">
        <SummaryCard label="총 활동" value={sum?.total ?? 0} suffix="건" />
        <SummaryCard
          label="기간"
          value={sum?.period_days ?? 30}
          suffix="일"
        />
        <SummaryCard
          label="검색"
          value={sum?.by_type?.search ?? 0}
          suffix="건"
        />
        <SummaryCard
          label="피드백"
          value={sum?.by_type?.feedback ?? 0}
          suffix="건"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          활동 유형
          <Select
            value={activityType}
            onChange={(e) => setActivityType(e.target.value)}
          >
            {ACTIVITY_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          시작일
          <Input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          종료일
          <Input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </label>
      </div>

      <p className="text-xs text-fg-muted">총 {total.toLocaleString()}건</p>

      {list.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, idx) => (
            <Skeleton key={idx} className="h-16" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorFallback
          title="활동 목록을 불러올 수 없습니다"
          error={list.error}
          onRetry={() => list.refetch()}
        />
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-8 text-center text-sm text-fg-muted">
          해당 기간에 활동 내역이 없습니다.
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((act, idx) => {
            const ts = fmtDate(act.created_at ?? act.timestamp);
            const detail = detailString(act.detail, act.metadata);
            const title = act.title ?? act.description ?? "(내용 없음)";
            return (
              <li
                key={act.id ?? `${ts}-${idx}`}
                className="flex items-start gap-3 rounded-md border border-border-default bg-bg-canvas px-4 py-3 text-sm"
              >
                <span className="mt-0.5 shrink-0">
                  <IconFor type={act.activity_type} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-fg-default">{title}</p>
                  {detail && (
                    <p className="mt-1 line-clamp-2 break-words text-xs text-fg-muted">
                      {detail}
                    </p>
                  )}
                </div>
                <span className="shrink-0 font-mono text-[10px] text-fg-subtle">
                  {ts}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function SummaryCard({
  label,
  value,
  suffix,
}: {
  label: string;
  value: number;
  suffix: string;
}) {
  return (
    <article className="rounded-lg border border-border-default bg-bg-canvas p-4">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-fg-default">
        {value.toLocaleString()}
        <span className="ml-1 text-sm font-normal text-fg-muted">{suffix}</span>
      </p>
    </article>
  );
}
