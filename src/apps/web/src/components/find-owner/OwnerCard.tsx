import { Badge, Card, CardBody, CardHeader, CardTitle } from "@/components/ui";
import type { Owner } from "@/lib/api/endpoints";

function trustTone(score: number | undefined) {
  if (score === undefined) return "neutral";
  if (score >= 0.8) return "success";
  if (score >= 0.5) return "accent";
  return "warning";
}

export function OwnerCard({ owner }: { owner: Owner }) {
  return (
    <Card padding="default" hoverable>
      <CardHeader>
        <div className="flex items-start gap-3">
          <CardTitle>{owner.name}</CardTitle>
          {typeof owner.trust_score === "number" && (
            <Badge tone={trustTone(owner.trust_score) as "success" | "accent" | "warning" | "neutral"}>
              신뢰도 {Math.round(owner.trust_score * 100)}%
            </Badge>
          )}
        </div>
        {(owner.team || owner.expertise?.length) && (
          <p className="text-xs text-fg-muted">
            {owner.team}
            {owner.team && owner.expertise && owner.expertise.length > 0 && " · "}
            {(owner.expertise ?? []).join(", ")}
          </p>
        )}
      </CardHeader>
      <CardBody>
        {owner.contact && (
          <p className="font-mono text-xs text-fg-muted">{owner.contact}</p>
        )}
        {(owner.documents ?? []).length > 0 && (
          <details className="mt-3 group">
            <summary className="cursor-pointer list-none text-xs text-fg-muted">
              담당 문서 {(owner.documents ?? []).length}개
              <span aria-hidden className="ml-1 transition-transform group-open:rotate-180">▾</span>
            </summary>
            <ul className="mt-2 space-y-1">
              {(owner.documents ?? []).slice(0, 20).map((d, idx) => {
                const title =
                  (d as { title?: string }).title ??
                  (d as { document_name?: string }).document_name ??
                  (d as { id?: string }).id ??
                  `문서 ${idx + 1}`;
                return (
                  <li key={idx} className="line-clamp-1 text-xs text-fg-default">
                    · {String(title)}
                  </li>
                );
              })}
            </ul>
          </details>
        )}
      </CardBody>
    </Card>
  );
}
