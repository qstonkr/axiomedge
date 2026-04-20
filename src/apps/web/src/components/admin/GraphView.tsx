"use client";

import { useMemo } from "react";

import { Skeleton } from "@/components/ui";
import { useGraphExpand } from "@/hooks/admin/useOps";
import type { GraphNeighbor } from "@/lib/api/endpoints";

/**
 * 외부 라이브러리 없이 SVG 로 한 노드의 1-hop 이웃을 radial (hub-and-spoke)
 * 레이아웃으로 시각화. force-directed 가 아니지만 시각적 효과는 비슷
 * (관계 한눈에). 30+ 이웃은 잘림 — backend 가 max_neighbors 로 제한.
 */
const W = 640;
const H = 420;
const CX = W / 2;
const CY = H / 2;
const HUB_R = 28;
const NODE_R = 14;
const ORBIT = Math.min(W, H) / 2 - 50;

function neighborPos(i: number, total: number): { x: number; y: number } {
  const angle = (i / total) * Math.PI * 2 - Math.PI / 2;
  return { x: CX + Math.cos(angle) * ORBIT, y: CY + Math.sin(angle) * ORBIT };
}

function neighborKey(n: GraphNeighbor, idx: number): string {
  return n.id ?? n.node_id ?? `n-${idx}`;
}

function neighborLabel(n: GraphNeighbor): string {
  return (n.name ?? n.label ?? n.id ?? n.node_id ?? "?").toString();
}

function neighborTone(t: string | undefined): string {
  // entity_type 별 fill — accent palette 안에서 바리에이션.
  if (!t) return "var(--color-accent-default)";
  const k = t.toLowerCase();
  if (k.includes("person") || k.includes("expert"))
    return "var(--color-success-default)";
  if (k.includes("kb") || k.includes("group"))
    return "var(--color-warning-default)";
  if (k.includes("doc") || k.includes("chunk"))
    return "var(--color-accent-default)";
  return "var(--color-fg-muted)";
}

export function GraphView({
  nodeId,
  hubLabel,
}: {
  nodeId: string;
  hubLabel?: string;
}) {
  const expand = useGraphExpand(nodeId);

  const layout = useMemo(() => {
    const neighbors = (expand.data?.neighbors ?? []).slice(0, 24);
    const edges = expand.data?.edges ?? [];
    const positions = neighbors.map((n, idx) => ({
      key: neighborKey(n, idx),
      label: neighborLabel(n),
      type: n.entity_type ?? n.type,
      ...neighborPos(idx, Math.max(1, neighbors.length)),
      data: n,
    }));
    return { positions, edges };
  }, [expand.data]);

  if (expand.isLoading) return <Skeleton className="h-[420px]" />;

  if (!expand.data || (layout.positions.length === 0 && !expand.data.error)) {
    return (
      <p className="rounded-md border border-dashed border-border-default bg-bg-subtle px-4 py-8 text-center text-xs text-fg-muted">
        선택한 노드에 이웃이 없습니다.
      </p>
    );
  }

  if (expand.data.error) {
    return (
      <p className="rounded-md border border-danger-default/30 bg-danger-subtle px-4 py-3 text-xs text-danger-default">
        그래프 확장 실패: {expand.data.error}
      </p>
    );
  }

  return (
    <div className="rounded-lg border border-border-default bg-bg-canvas p-3">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block w-full"
        role="img"
        aria-label={`${hubLabel ?? nodeId} 의 1-hop 이웃 그래프`}
      >
        {/* edges (line from hub to each neighbor) */}
        {layout.positions.map((p) => (
          <line
            key={`e-${p.key}`}
            x1={CX}
            y1={CY}
            x2={p.x}
            y2={p.y}
            stroke="var(--color-border-strong)"
            strokeWidth={1}
            opacity={0.6}
          />
        ))}

        {/* hub node */}
        <g>
          <circle
            cx={CX}
            cy={CY}
            r={HUB_R}
            fill="var(--color-accent-default)"
            stroke="var(--color-accent-emphasis)"
            strokeWidth={2}
          />
          <text
            x={CX}
            y={CY + 4}
            textAnchor="middle"
            fontSize={11}
            fill="var(--color-fg-onAccent)"
            fontWeight={600}
          >
            {(hubLabel ?? nodeId).slice(0, 12)}
          </text>
        </g>

        {/* neighbor nodes */}
        {layout.positions.map((p) => (
          <g key={`n-${p.key}`}>
            <circle
              cx={p.x}
              cy={p.y}
              r={NODE_R}
              fill={neighborTone(p.type)}
              stroke="var(--color-bg-canvas)"
              strokeWidth={2}
            />
            <text
              x={p.x}
              y={p.y + NODE_R + 12}
              textAnchor="middle"
              fontSize={10}
              fill="var(--color-fg-default)"
            >
              {p.label.slice(0, 14)}
            </text>
          </g>
        ))}
      </svg>

      <ul className="mt-3 grid gap-1 text-xs sm:grid-cols-2">
        {layout.positions.map((p) => (
          <li
            key={`l-${p.key}`}
            className="flex items-center gap-2 truncate text-fg-muted"
            title={`${p.label} · ${p.type ?? ""}`}
          >
            <span
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{ background: neighborTone(p.type) }}
            />
            <span className="truncate">{p.label}</span>
            {p.type && (
              <span className="shrink-0 font-mono text-[10px] text-fg-subtle">
                {p.type}
              </span>
            )}
          </li>
        ))}
      </ul>

      <p className="mt-2 text-[10px] text-fg-subtle">
        ※ 단순 hub-and-spoke 레이아웃 (1-hop 이웃 24개 표시). 더 깊은 탐색은
        backend{" "}
        <code className="font-mono">/admin/graph/expand</code> 또는 Streamlit
        graph_explorer 사용.
      </p>
    </div>
  );
}
