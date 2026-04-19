import { Badge, Card } from "@/components/ui";

import type { ChunkSource } from "./types";

const TIER_TONE: Record<string, "accent" | "success" | "warning" | "neutral"> = {
  global: "accent",
  team: "success",
  personal: "warning",
};

export function SourceCard({
  chunk,
  onReportError,
}: {
  chunk: ChunkSource;
  onReportError?: (chunk: ChunkSource) => void;
}) {
  const text = chunk.text ?? chunk.content ?? "";
  const title =
    chunk.document_name ?? chunk.title ?? chunk.document_id ?? chunk.id ?? "—";
  const tone = chunk.tier ? (TIER_TONE[chunk.tier] ?? "neutral") : "neutral";
  const score = chunk.rerank_score ?? chunk.score;

  return (
    <Card padding="compact" className="space-y-2">
      <header className="flex items-center gap-2">
        <span className="line-clamp-1 flex-1 text-sm font-medium text-fg-default">
          {title}
        </span>
        {chunk.tier && <Badge tone={tone}>{chunk.tier}</Badge>}
        {typeof score === "number" && (
          <span className="font-mono text-xs tabular-nums text-fg-muted">
            {score.toFixed(2)}
          </span>
        )}
      </header>
      {text && (
        <p className="line-clamp-3 text-sm leading-6 text-fg-muted">{text}</p>
      )}
      <footer className="flex items-center justify-between text-xs text-fg-subtle">
        <span className="font-mono">{chunk.kb_id ?? ""}</span>
        {onReportError && (
          <button
            type="button"
            onClick={() => onReportError(chunk)}
            className="rounded-md px-2 py-1 transition-colors hover:bg-bg-muted hover:text-fg-default"
          >
            오류 신고
          </button>
        )}
      </footer>
    </Card>
  );
}
