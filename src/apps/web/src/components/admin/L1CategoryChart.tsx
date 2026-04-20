"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Select, Skeleton } from "@/components/ui";
import { useKbCategories } from "@/hooks/admin/useOps";
import { useSearchableKbs } from "@/hooks/useSearch";

/**
 * KB 의 L1 카테고리 분포 — Streamlit `dashboard.py` 의 L1 카테고리 탭 이식.
 * Qdrant payload `l1_category` 별 문서 수를 backend 가 집계,
 * 상위 카테고리 BarChart + 전체 list.
 */
export function L1CategoryChart() {
  const kbs = useSearchableKbs();
  const [kbId, setKbId] = useState("");
  const cats = useKbCategories(kbId || null);

  const data = cats.data?.categories ?? [];
  const top = data.slice(0, 12);

  return (
    <article className="space-y-3">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-fg-default">
            📂 L1 카테고리 분포
          </h2>
          <p className="text-xs text-fg-muted">
            KB 별 문서의 1차 카테고리 (l1_category payload) 집계.
          </p>
        </div>
        <label className="block w-64 space-y-1 text-xs font-medium text-fg-muted">
          KB 선택
          <Select value={kbId} onChange={(e) => setKbId(e.target.value)}>
            <option value="">— KB 선택 —</option>
            {(kbs.data ?? []).map((kb) => (
              <option key={kb.kb_id} value={kb.kb_id}>
                {kb.name} ({kb.kb_id})
              </option>
            ))}
          </Select>
        </label>
      </header>

      {!kbId ? (
        <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
          KB 를 선택하면 카테고리 분포가 표시됩니다.
        </p>
      ) : cats.isLoading ? (
        <Skeleton className="h-72" />
      ) : data.length === 0 ? (
        <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-6 text-center text-xs text-fg-muted">
          이 KB 에 카테고리가 분류된 문서가 없습니다.
        </p>
      ) : (
        <>
          <div
            className="rounded-lg border border-border-default bg-bg-canvas p-3"
            style={{ height: 260 }}
          >
            <ResponsiveContainer>
              <BarChart
                data={top}
                margin={{ top: 10, right: 10, left: -10, bottom: 30 }}
              >
                <CartesianGrid
                  stroke="var(--color-border-default)"
                  strokeDasharray="3 3"
                />
                <XAxis
                  dataKey="name"
                  tick={{ fill: "var(--color-fg-subtle)", fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                  interval={0}
                  angle={-25}
                  textAnchor="end"
                />
                <YAxis
                  tick={{ fill: "var(--color-fg-subtle)", fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                  width={40}
                />
                <RechartsTooltip
                  contentStyle={{
                    background: "var(--color-bg-canvas)",
                    border: "1px solid var(--color-border-default)",
                    borderRadius: 6,
                    fontSize: 12,
                    color: "var(--color-fg-default)",
                  }}
                />
                <Bar
                  dataKey="document_count"
                  fill="var(--color-accent-default)"
                  radius={[4, 4, 0, 0]}
                  isAnimationActive={false}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
          {data.length > 12 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-fg-muted">
                전체 {data.length}개 카테고리 보기
              </summary>
              <ul className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
                {data.map((c) => (
                  <li
                    key={c.name}
                    className="flex justify-between rounded border border-border-default/60 bg-bg-canvas px-2 py-1"
                  >
                    <span className="truncate text-fg-default" title={c.name}>
                      {c.name}
                    </span>
                    <span className="font-mono tabular-nums text-fg-muted">
                      {c.document_count.toLocaleString()}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </article>
  );
}
