import { Badge } from "@/components/ui";

import type { AssistantTurn } from "./types";

const LEVEL_BADGE: Record<
  string,
  { label: string; tone: "success" | "warning" | "danger" }
> = {
  HIGH: { label: "🟢 HIGH", tone: "success" },
  MEDIUM: { label: "🟡 MEDIUM", tone: "warning" },
  LOW: { label: "🟠 LOW", tone: "warning" },
  UNCERTAIN: { label: "🔴 UNCERTAIN", tone: "danger" },
};

/**
 * Render small inline metadata pills below the answer + Composite Reranking
 * 점수 분해 expander (Streamlit chat.py 의 _render_meta_signals 동치).
 */
export function MetaSignals({ meta }: { meta: AssistantTurn["meta"] }) {
  if (!meta) return null;

  const items: {
    label: string;
    value: string;
    tone?: "neutral" | "accent" | "warning" | "success" | "danger";
  }[] = [];
  if (meta.confidence_level) {
    const lv = LEVEL_BADGE[String(meta.confidence_level).toUpperCase()];
    if (lv) items.push({ label: "신뢰도", value: lv.label, tone: lv.tone });
    else items.push({ label: "신뢰도", value: String(meta.confidence_level) });
  } else if (meta.confidence !== undefined && meta.confidence !== "") {
    items.push({
      label: "신뢰도",
      value:
        typeof meta.confidence === "number"
          ? `${Math.round(meta.confidence * 100)}%`
          : meta.confidence,
    });
  }
  if (meta.crag_action)
    items.push({ label: "CRAG", value: meta.crag_action, tone: "warning" });
  if (meta.query_type) items.push({ label: "유형", value: meta.query_type });
  if (meta.iteration_count !== undefined)
    items.push({ label: "반복", value: String(meta.iteration_count) });
  if (meta.search_time_ms !== undefined)
    items.push({ label: "응답", value: `${Math.round(meta.search_time_ms)}ms` });
  if (meta.estimated_cost_usd !== undefined && meta.estimated_cost_usd > 0)
    items.push({
      label: "비용",
      value: `$${meta.estimated_cost_usd.toFixed(4)}`,
    });
  if (meta.llm_provider)
    items.push({ label: "LLM", value: meta.llm_provider, tone: "accent" });
  if (meta.working_memory_hit)
    items.push({ label: "WM probe", value: "hit", tone: "accent" });

  const breakdown = meta.rerank_breakdown;
  const hasBreakdown =
    breakdown !== undefined &&
    Object.values(breakdown).some(
      (v) => typeof v === "number" && Number.isFinite(v) && v !== 0,
    );

  const corrected =
    meta.corrected_query &&
    meta.original_query &&
    meta.corrected_query !== meta.original_query
      ? { from: meta.original_query, to: meta.corrected_query }
      : null;

  if (
    items.length === 0 &&
    !hasBreakdown &&
    !corrected &&
    !(meta.expanded_terms && meta.expanded_terms.length > 0)
  ) {
    return null;
  }

  return (
    <div className="space-y-2">
      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {items.map((item) => (
            <Badge
              key={`${item.label}:${item.value}`}
              tone={item.tone ?? "neutral"}
            >
              {item.label} · {item.value}
            </Badge>
          ))}
        </div>
      )}

      {corrected && (
        <p className="text-xs text-fg-muted">
          ✏️ 오타 교정: <span className="font-mono">{corrected.from}</span> →{" "}
          <span className="font-mono text-fg-default">{corrected.to}</span>
        </p>
      )}

      {meta.expanded_terms && meta.expanded_terms.length > 0 && (
        <p className="text-xs text-fg-muted">
          🔍 쿼리 확장:{" "}
          <span className="font-mono">{meta.expanded_terms.join(", ")}</span>
        </p>
      )}

      {hasBreakdown && breakdown && (
        <details className="rounded-md border border-border-default bg-bg-subtle px-3 py-2 text-xs">
          <summary className="cursor-pointer text-fg-muted">
            📊 Composite Reranking 점수 분해
          </summary>
          <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {(
              [
                ["Dense", breakdown.dense],
                ["Sparse", breakdown.sparse],
                ["ColBERT", breakdown.colbert],
                ["Cross-Enc", breakdown.cross_encoder],
              ] as const
            ).map(([label, v]) => (
              <div
                key={label}
                className="rounded border border-border-default/60 bg-bg-canvas px-2 py-1.5"
              >
                <div className="text-[10px] text-fg-muted">{label}</div>
                <div className="font-mono tabular-nums text-fg-default">
                  {typeof v === "number" ? v.toFixed(3) : "—"}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
