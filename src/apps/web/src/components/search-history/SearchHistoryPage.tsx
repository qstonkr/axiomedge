"use client";

import { useMemo, useState } from "react";

import {
  Button,
  EmptyState,
  ErrorFallback,
  Input,
  Select,
  Skeleton,
} from "@/components/ui";
import { useSearchHistory } from "@/hooks/useMyDocuments";

const PAGE_SIZES = [20, 50, 100];

export function SearchHistoryPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [queryFilter, setQueryFilter] = useState("");
  const [dateStart, setDateStart] = useState("");
  const [dateEnd, setDateEnd] = useState("");

  const { data, isLoading, isError, error, refetch } = useSearchHistory({
    page,
    page_size: pageSize,
  });

  const filtered = useMemo(() => {
    const items = data?.items ?? [];
    return items.filter((it) => {
      if (queryFilter && !it.query.toLowerCase().includes(queryFilter.toLowerCase()))
        return false;
      const ts = it.timestamp?.slice(0, 10) ?? "";
      if (dateStart && ts < dateStart) return false;
      if (dateEnd && ts > dateEnd) return false;
      return true;
    });
  }, [data?.items, queryFilter, dateStart, dateEnd]);

  const stats = useMemo(() => {
    const count = filtered.length;
    const avgResults =
      count === 0
        ? 0
        : filtered.reduce((s, it) => s + (it.result_count ?? 0), 0) / count;
    const avgMs =
      count === 0
        ? 0
        : filtered.reduce((s, it) => s + (it.response_time_ms ?? 0), 0) / count;
    return { count, avgResults, avgMs };
  }, [filtered]);

  const total = data?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));

  return (
    <section className="mx-auto w-full max-w-5xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold leading-snug text-fg-default">
          🕐 검색 이력
        </h1>
        <p className="text-sm text-fg-muted">
          최근 검색 기록과 결과 통계를 한눈에 확인합니다.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          검색어 필터
          <Input
            value={queryFilter}
            onChange={(e) => setQueryFilter(e.target.value)}
            placeholder="쿼리 부분 일치"
          />
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          시작일
          <Input
            type="date"
            value={dateStart}
            onChange={(e) => setDateStart(e.target.value)}
          />
        </label>
        <label className="block space-y-1 text-xs font-medium text-fg-muted">
          종료일
          <Input
            type="date"
            value={dateEnd}
            onChange={(e) => setDateEnd(e.target.value)}
          />
        </label>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label="조회수" value={String(stats.count)} />
        <StatCard
          label="평균 결과 수"
          value={stats.avgResults.toFixed(1)}
        />
        <StatCard
          label="평균 응답 시간"
          value={`${Math.round(stats.avgMs)}ms`}
        />
      </div>

      <div className="flex items-center gap-3 text-xs text-fg-muted">
        <span id="page-size-label">페이지 크기</span>
        <Select
          aria-labelledby="page-size-label"
          value={pageSize}
          onChange={(e) => {
            setPageSize(Number(e.target.value));
            setPage(1);
          }}
          className="w-24"
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </Select>
        <span className="ml-auto">
          {page} / {lastPage} (총 {total})
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          ← 이전
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={page >= lastPage}
          onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
        >
          다음 →
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, idx) => (
            <Skeleton key={idx} className="h-10" />
          ))}
        </div>
      ) : isError ? (
        <ErrorFallback
          title="검색 이력을 불러올 수 없습니다"
          error={error}
          onRetry={() => refetch()}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="🔎"
          title="조건에 맞는 검색 이력이 없습니다"
          description="필터를 조정하거나 다음 페이지로 이동해 보세요."
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-border-default">
          <table className="min-w-full divide-y divide-border-default text-sm">
            <thead className="bg-bg-subtle text-xs text-fg-muted">
              <tr>
                <th className="px-3 py-2 text-left font-medium">시각</th>
                <th className="px-3 py-2 text-left font-medium">검색어</th>
                <th className="px-3 py-2 text-right font-medium">결과</th>
                <th className="px-3 py-2 text-right font-medium">응답</th>
                <th className="px-3 py-2 text-left font-medium">KB</th>
                <th className="px-3 py-2 text-left font-medium">소스</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-default bg-bg-canvas">
              {filtered.map((it, idx) => (
                <tr key={`${it.timestamp}-${idx}`}>
                  <td className="px-3 py-2 font-mono text-xs text-fg-muted">
                    {it.timestamp?.slice(0, 19).replace("T", " ")}
                  </td>
                  <td className="px-3 py-2 text-fg-default">{it.query}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-fg-default">
                    {it.result_count ?? 0}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-fg-muted">
                    {it.response_time_ms ?? 0}ms
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-fg-subtle">
                    {(it.kb_ids ?? []).join(", ")}
                  </td>
                  <td className="px-3 py-2 text-xs text-fg-muted">
                    {it.source ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 검색 이벤트 타임라인 — 위 표와 동일 데이터를 chronological 로 정리.
          event_type 이 있으면 icon, 없으면 검색 default. Streamlit
          search_history.py 의 LineageEventType 패턴 이식. */}
      {!isLoading && !isError && filtered.length > 0 && (
        <section aria-labelledby="timeline-heading" className="space-y-3 pt-4">
          <h2
            id="timeline-heading"
            className="text-sm font-medium text-fg-default"
          >
            🕒 검색 이벤트 타임라인
          </h2>
          <ol className="space-y-1.5 border-l-2 border-border-default pl-4">
            {filtered.slice(0, 30).map((it, idx) => {
              const ts = it.timestamp?.slice(0, 19).replace("T", " ") ?? "";
              const evt = (
                it as { event_type?: string }
              ).event_type as string | undefined;
              const icon = eventIcon(evt);
              return (
                <li
                  key={`${ts}-${idx}`}
                  className="relative grid grid-cols-[20px_120px_minmax(0,1fr)] items-start gap-3 text-xs"
                >
                  <span
                    aria-hidden
                    className="-ml-[26px] inline-flex h-5 w-5 items-center justify-center rounded-full bg-bg-canvas text-base ring-2 ring-border-default"
                  >
                    {icon}
                  </span>
                  <span className="font-mono text-fg-muted">{ts}</span>
                  <span className="break-words text-fg-default">
                    {evt && (
                      <span className="mr-2 rounded bg-bg-muted px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
                        {evt}
                      </span>
                    )}
                    &ldquo;{it.query}&rdquo;{" "}
                    <span className="text-fg-subtle">
                      ({it.result_count ?? 0}건)
                    </span>
                  </span>
                </li>
              );
            })}
          </ol>
          {filtered.length > 30 && (
            <p className="text-xs text-fg-subtle">
              최근 30건만 표시 — 더 보려면 위 표 페이지네이션 사용.
            </p>
          )}
        </section>
      )}
    </section>
  );
}

function eventIcon(type: string | undefined): string {
  switch (type) {
    case "CREATED":
      return "🆕";
    case "UPDATED":
      return "📝";
    case "MERGED":
      return "🔀";
    case "SPLIT":
      return "✂️";
    case "ARCHIVED":
      return "📦";
    case "RESTORED":
      return "♻️";
    case "LINKED":
      return "🔗";
    case "UNLINKED":
      return "🔓";
    case "MIGRATED":
      return "🚚";
    default:
      return "🔍";
  }
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border-default bg-bg-canvas px-4 py-3">
      <div className="text-xs text-fg-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums text-fg-default">
        {value}
      </div>
    </div>
  );
}
