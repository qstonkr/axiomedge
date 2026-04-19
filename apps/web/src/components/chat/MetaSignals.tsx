import { Badge } from "@/components/ui";

import type { AssistantTurn } from "./types";

/** Render small inline metadata pills below the answer (compact, scannable). */
export function MetaSignals({ meta }: { meta: AssistantTurn["meta"] }) {
  if (!meta) return null;

  const items: { label: string; value: string; tone?: "neutral" | "accent" | "warning" }[] = [];
  if (meta.confidence !== undefined && meta.confidence !== "")
    items.push({
      label: "신뢰도",
      value: typeof meta.confidence === "number"
        ? `${Math.round(meta.confidence * 100)}%`
        : meta.confidence,
    });
  if (meta.crag_action)
    items.push({ label: "CRAG", value: meta.crag_action, tone: "warning" });
  if (meta.query_type)
    items.push({ label: "유형", value: meta.query_type });
  if (meta.iteration_count !== undefined)
    items.push({ label: "반복", value: String(meta.iteration_count) });
  if (meta.search_time_ms !== undefined)
    items.push({ label: "응답", value: `${Math.round(meta.search_time_ms)}ms` });
  if (meta.estimated_cost_usd !== undefined && meta.estimated_cost_usd > 0)
    items.push({ label: "비용", value: `$${meta.estimated_cost_usd.toFixed(4)}` });
  if (meta.llm_provider)
    items.push({ label: "LLM", value: meta.llm_provider, tone: "accent" });

  if (items.length === 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {items.map((item) => (
        <Badge key={`${item.label}:${item.value}`} tone={item.tone ?? "neutral"}>
          {item.label} · {item.value}
        </Badge>
      ))}
    </div>
  );
}
