"use client";

import { useMemo, useState } from "react";

import {
  Button,
  EmptyState,
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

  const { data, isLoading } = useSearchHistory({ page, page_size: pageSize });

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
    </section>
  );
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
