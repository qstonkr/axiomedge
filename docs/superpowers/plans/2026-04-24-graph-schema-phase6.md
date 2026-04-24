# GraphRAG Schema Evolution — Phase 4b + 5b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Close the operator-facing loop of the schema evolution system.
Phase 4b builds the Next.js admin page for candidate review + run triggers
(spec §5.2 + §5.6). Phase 5b adds a thin Slack notification layer for the
three alert events spec §5.6 calls for, plus an integration test that
exercises bootstrap → approve → YAML flow end-to-end against stubbed LLM.

**Architecture:**

- **Next.js admin page** under `(admin)/admin/graph-schema/` — a single
  client component that lists pending candidates (table), lets admins
  approve/reject/merge/rename inline, and triggers bootstrap or
  re-extract. All data comes through the existing BFF proxy; no new
  server code on the web side.
- **Slack module** `src/notifications/slack.py` — one `send()` function
  that reads `SLACK_WEBHOOK_URL` from settings, posts a JSON block via
  `httpx`, and swallows all failures (logs, never throws). Three
  event wrappers (`bootstrap_failed_thrice`, `pending_threshold`,
  `yaml_pr_stale`). Called from `schema_bootstrap_jobs.py` and a
  cron `schema_alerts_sweep`.
- **Integration test** `tests/integration/test_kb_onboarding.py`
  stubs LLM + Neo4j at the boundary, walks the full onboarding flow.

**Spec reference:** `docs/superpowers/specs/2026-04-24-graph-schema-evolution-design.md`
§5.2 (Admin review), §5.4 (end-user transparency), §5.6 (Ops — Slack),
§5.7 (first-time onboarding flow).

**Out of scope (deferred):** Streamlit graph-schema page (Next.js is the
primary admin per user memory), Prometheus metrics wiring (separate infra
PR), email notifications (Slack only in this phase), Phase 6 realtime
schema evolution.

---

## File Structure

### New files (Phase 4b — Next.js)

| Path | Responsibility |
|---|---|
| `src/apps/web/src/app/(admin)/admin/graph-schema/page.tsx` | Next.js route — metadata + client wrapper |
| `src/apps/web/src/components/admin/GraphSchemaClient.tsx` | Table + approve/reject/merge/rename dialogs + trigger buttons |
| `src/apps/web/src/hooks/admin/useGraphSchema.ts` | React-Query hooks |
| `src/apps/web/tests/unit/graph-schema.test.tsx` | Vitest component test (smoke + happy path for approve) |

### New files (Phase 5b — Slack + E2E)

| Path | Responsibility |
|---|---|
| `src/notifications/__init__.py` | Package marker |
| `src/notifications/slack.py` | `send(text)` + three event helpers |
| `src/jobs/schema_alerts.py` | arq cron: scans for threshold breaches, emits Slack |
| `tests/unit/test_slack_notifications.py` | Unit tests (httpx mock) |
| `tests/unit/test_schema_alerts.py` | Unit test for cron |
| `tests/integration/test_kb_onboarding.py` | End-to-end flow (stubbed deps) |

### Modified

| Path | Change |
|---|---|
| `src/apps/web/src/lib/api/endpoints.ts` | Add 6 graph-schema endpoint fns + types |
| `src/apps/web/src/components/admin/AdminSidebar.tsx` | Add "Graph Schema" link |
| `src/config/settings.py` | Add `NotificationSettings` (SLACK_WEBHOOK_URL, candidate_pending_threshold) |
| `src/jobs/tasks.py` | Register `schema_alerts_sweep` |
| `src/jobs/worker.py` | Add cron for sweep |

---

## Task 1: Web — endpoint types + hooks

**Files:** `src/apps/web/src/lib/api/endpoints.ts` + new
`src/apps/web/src/hooks/admin/useGraphSchema.ts`.

- [ ] **Step 1: Append endpoint types to `endpoints.ts`**

Append at the bottom (before the final newline):

```typescript
// ── /admin/graph-schema ──
export type GraphSchemaCandidate = {
  id: string;
  kb_id: string;
  candidate_type: "node" | "relationship";
  label: string;
  frequency: number;
  confidence_avg: number;
  confidence_min: number;
  confidence_max: number;
  source_label: string | null;
  target_label: string | null;
  examples: Array<Record<string, unknown>>;
  similar_labels: Array<Record<string, unknown>>;
};

export const listGraphSchemaCandidates = async (
  kb_id: string,
): Promise<{ candidates: GraphSchemaCandidate[] }> => {
  return request<{ candidates: GraphSchemaCandidate[] }>(
    `api/v1/admin/graph-schema/candidates?kb_id=${encodeURIComponent(kb_id)}`,
    { method: "GET" },
  );
};

export type GraphSchemaDecideBody = {
  kb_id: string;
  candidate_type: "node" | "relationship";
  label: string;
};

export const approveGraphSchemaCandidate = async (
  body: GraphSchemaDecideBody & { approved_by: string },
) =>
  request<{ status: string; yaml_path: string; git: unknown }>(
    "api/v1/admin/graph-schema/candidates/approve",
    { method: "POST", body: JSON.stringify(body) },
  );

export const rejectGraphSchemaCandidate = async (
  body: GraphSchemaDecideBody & { decided_by: string; reason?: string },
) =>
  request<{ status: string }>(
    "api/v1/admin/graph-schema/candidates/reject",
    { method: "POST", body: JSON.stringify(body) },
  );

export const mergeGraphSchemaCandidate = async (
  body: GraphSchemaDecideBody & { merge_into: string; decided_by: string },
) =>
  request<{ status: string; merged_into: string }>(
    "api/v1/admin/graph-schema/candidates/merge",
    { method: "POST", body: JSON.stringify(body) },
  );

export const renameGraphSchemaCandidate = async (
  body: GraphSchemaDecideBody & { new_label: string; approved_by: string },
) =>
  request<{ status: string; new_label: string; yaml_path: string }>(
    "api/v1/admin/graph-schema/candidates/rename",
    { method: "POST", body: JSON.stringify(body) },
  );

export const triggerGraphSchemaBootstrap = async (
  kb_id: string,
  body: { triggered_by_user?: string } = {},
) =>
  request<{ status: string; job_id?: string }>(
    `api/v1/admin/graph-schema/bootstrap/${encodeURIComponent(kb_id)}/run`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const triggerGraphSchemaReextract = async (
  kb_id: string,
  body: { triggered_by_user: string },
) =>
  request<{
    status: string;
    reextract_job_id: string;
    schema_version_from: number;
    schema_version_to: number;
  }>(
    `api/v1/admin/graph-schema/reextract/${encodeURIComponent(kb_id)}/run`,
    { method: "POST", body: JSON.stringify(body) },
  );
```

- [ ] **Step 2: Create hook file**

Create `src/apps/web/src/hooks/admin/useGraphSchema.ts`:

```typescript
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveGraphSchemaCandidate,
  listGraphSchemaCandidates,
  mergeGraphSchemaCandidate,
  rejectGraphSchemaCandidate,
  renameGraphSchemaCandidate,
  triggerGraphSchemaBootstrap,
  triggerGraphSchemaReextract,
  type GraphSchemaCandidate,
} from "@/lib/api/endpoints";

export function useGraphSchemaCandidates(kb_id: string) {
  return useQuery<{ candidates: GraphSchemaCandidate[] }>({
    queryKey: ["admin", "graph-schema", "candidates", kb_id],
    queryFn: () => listGraphSchemaCandidates(kb_id),
    enabled: Boolean(kb_id),
    staleTime: 30 * 1000,
  });
}

function useDecideMutation<TBody>(
  fn: (body: TBody) => Promise<unknown>,
  kb_id: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["admin", "graph-schema", "candidates", kb_id],
      });
    },
  });
}

export function useApproveCandidate(kb_id: string) {
  return useDecideMutation(approveGraphSchemaCandidate, kb_id);
}
export function useRejectCandidate(kb_id: string) {
  return useDecideMutation(rejectGraphSchemaCandidate, kb_id);
}
export function useMergeCandidate(kb_id: string) {
  return useDecideMutation(mergeGraphSchemaCandidate, kb_id);
}
export function useRenameCandidate(kb_id: string) {
  return useDecideMutation(renameGraphSchemaCandidate, kb_id);
}

export function useTriggerBootstrap() {
  return useMutation({
    mutationFn: (kb_id: string) => triggerGraphSchemaBootstrap(kb_id),
  });
}

export function useTriggerReextract() {
  return useMutation({
    mutationFn: ({ kb_id, triggered_by_user }: {
      kb_id: string; triggered_by_user: string;
    }) => triggerGraphSchemaReextract(kb_id, { triggered_by_user }),
  });
}
```

- [ ] **Step 3: Typecheck + commit**

```bash
cd src/apps/web && pnpm exec tsc --noEmit 2>&1 | head -20 && cd ../../..
git add src/apps/web/src/lib/api/endpoints.ts src/apps/web/src/hooks/admin/useGraphSchema.ts
git commit -m "feat(web): graph-schema API client hooks"
```

---

## Task 2: Web — admin page + client component

**Files:** `src/apps/web/src/app/(admin)/admin/graph-schema/page.tsx` +
`src/apps/web/src/components/admin/GraphSchemaClient.tsx` + sidebar link.

- [ ] **Step 1: Create route**

Create `src/apps/web/src/app/(admin)/admin/graph-schema/page.tsx`:

```typescript
import { GraphSchemaClient } from "@/components/admin/GraphSchemaClient";

export const metadata = { title: "그래프 스키마 · axiomedge admin" };

export default function AdminGraphSchemaRoute() {
  return <GraphSchemaClient />;
}
```

- [ ] **Step 2: Create client component**

Create `src/apps/web/src/components/admin/GraphSchemaClient.tsx`:

```typescript
"use client";

import { useState } from "react";

import { Badge, Button, Input, Skeleton, useToast } from "@/components/ui";
import {
  useApproveCandidate,
  useGraphSchemaCandidates,
  useMergeCandidate,
  useRejectCandidate,
  useRenameCandidate,
  useTriggerBootstrap,
  useTriggerReextract,
} from "@/hooks/admin/useGraphSchema";
import { useSearchableKbs } from "@/hooks/useSearch";
import type { GraphSchemaCandidate } from "@/lib/api/endpoints";

import { DataTable, type Column } from "./DataTable";

const ADMIN_USER = "admin@web";

export function GraphSchemaClient() {
  const toast = useToast();
  const { data: kbs } = useSearchableKbs();
  const [kbId, setKbId] = useState<string>("");
  const { data, isLoading, isError, error } = useGraphSchemaCandidates(kbId);

  const approve = useApproveCandidate(kbId);
  const reject = useRejectCandidate(kbId);
  const merge = useMergeCandidate(kbId);
  const rename = useRenameCandidate(kbId);
  const bootstrap = useTriggerBootstrap();
  const reextract = useTriggerReextract();

  async function onApprove(c: GraphSchemaCandidate) {
    if (!confirm(`'${c.label}' 을(를) 승인하시겠습니까?`)) return;
    try {
      await approve.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        approved_by: ADMIN_USER,
      });
      toast.push("승인 — YAML 업데이트됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "승인 실패", "danger");
    }
  }

  async function onReject(c: GraphSchemaCandidate) {
    const reason = prompt("거부 사유 (선택)", "");
    if (reason === null) return;
    try {
      await reject.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        decided_by: ADMIN_USER,
        reason: reason || undefined,
      });
      toast.push("거부됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "거부 실패", "danger");
    }
  }

  async function onMerge(c: GraphSchemaCandidate) {
    const target = prompt(`'${c.label}' 을(를) 어느 라벨로 병합?`, "");
    if (!target) return;
    try {
      await merge.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        merge_into: target,
        decided_by: ADMIN_USER,
      });
      toast.push(`병합 → '${target}'`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "병합 실패", "danger");
    }
  }

  async function onRename(c: GraphSchemaCandidate) {
    const next = prompt(`'${c.label}' 을(를) 어떤 이름으로 승인?`, c.label);
    if (!next || next === c.label) return;
    try {
      await rename.mutateAsync({
        kb_id: c.kb_id,
        candidate_type: c.candidate_type,
        label: c.label,
        new_label: next,
        approved_by: ADMIN_USER,
      });
      toast.push(`승인 (이름 변경: '${next}')`, "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "이름 변경 실패", "danger");
    }
  }

  async function onBootstrap() {
    if (!kbId) return;
    try {
      await bootstrap.mutateAsync(kbId);
      toast.push("Bootstrap 큐 등록됨", "success");
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "Bootstrap 실패", "danger");
    }
  }

  async function onReextract() {
    if (!kbId) return;
    if (!confirm(`'${kbId}' 를 현재 스키마로 전체 재추출합니다. 계속?`)) return;
    try {
      const res = await reextract.mutateAsync({
        kb_id: kbId, triggered_by_user: ADMIN_USER,
      });
      toast.push(
        `재추출 큐 등록 (v${res.schema_version_from} → v${res.schema_version_to})`,
        "success",
      );
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "재추출 실패", "danger");
    }
  }

  const candidates = data?.candidates ?? [];

  const columns: Column<GraphSchemaCandidate>[] = [
    {
      key: "candidate_type",
      header: "종류",
      render: (c) => (
        <Badge tone={c.candidate_type === "node" ? "accent" : "neutral"}>
          {c.candidate_type}
        </Badge>
      ),
    },
    {
      key: "label",
      header: "라벨",
      render: (c) => (
        <span className="font-mono text-fg-default">{c.label}</span>
      ),
    },
    {
      key: "frequency",
      header: "빈도",
      render: (c) => <span>{c.frequency}</span>,
    },
    {
      key: "confidence_avg",
      header: "신뢰도",
      render: (c) => (
        <span>
          {c.confidence_avg.toFixed(2)}{" "}
          <span className="text-fg-subtle">
            ({c.confidence_min.toFixed(2)}–{c.confidence_max.toFixed(2)})
          </span>
        </span>
      ),
    },
    {
      key: "similar_labels",
      header: "유사 라벨",
      render: (c) => (
        <span className="text-fg-muted">
          {c.similar_labels.length > 0
            ? c.similar_labels.map((s) =>
                typeof s === "object" && s !== null && "label" in s
                  ? String((s as { label: unknown }).label)
                  : "",
              ).filter(Boolean).join(", ")
            : "—"}
        </span>
      ),
    },
    {
      key: "actions",
      header: "작업",
      render: (c) => (
        <div className="flex gap-1">
          <Button size="sm" tone="primary" onClick={() => onApprove(c)}>
            승인
          </Button>
          <Button size="sm" tone="neutral" onClick={() => onRename(c)}>
            이름
          </Button>
          <Button size="sm" tone="neutral" onClick={() => onMerge(c)}>
            병합
          </Button>
          <Button size="sm" tone="danger" onClick={() => onReject(c)}>
            거부
          </Button>
        </div>
      ),
    },
  ];

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-fg-default">그래프 스키마</h1>
        <div className="flex items-center gap-2">
          <Input
            list="kb-options"
            placeholder="kb_id"
            value={kbId}
            onChange={(e) => setKbId(e.target.value)}
            className="w-40"
          />
          <datalist id="kb-options">
            {(kbs?.kbs ?? []).map((k) => (
              <option key={k.id} value={k.id} />
            ))}
          </datalist>
          <Button
            size="sm" tone="neutral"
            onClick={onBootstrap} disabled={!kbId || bootstrap.isPending}
          >
            Bootstrap
          </Button>
          <Button
            size="sm" tone="primary"
            onClick={onReextract} disabled={!kbId || reextract.isPending}
          >
            재추출
          </Button>
        </div>
      </header>

      {!kbId && (
        <p className="text-fg-muted">검토할 KB를 선택하세요.</p>
      )}

      {kbId && isLoading && <Skeleton className="h-48 w-full" />}
      {kbId && isError && (
        <p className="text-fg-danger">
          {error instanceof Error ? error.message : "불러오기 실패"}
        </p>
      )}
      {kbId && !isLoading && !isError && (
        <DataTable
          data={candidates}
          columns={columns}
          emptyHint="대기 중인 후보가 없습니다."
        />
      )}
    </section>
  );
}
```

- [ ] **Step 3: Add sidebar link**

Find the existing sidebar entries in `src/apps/web/src/components/admin/AdminSidebar.tsx` and add a `Graph Schema` link between Glossary and Lifecycle (or wherever makes sense — the existing adjacent sibling is `/admin/glossary`). Use the existing link pattern in that file.

- [ ] **Step 4: Smoke test**

Create `src/apps/web/tests/unit/graph-schema.test.tsx`:

```typescript
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// Mock endpoints before importing the component.
vi.mock("@/lib/api/endpoints", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/endpoints")>(
    "@/lib/api/endpoints",
  );
  return {
    ...actual,
    listGraphSchemaCandidates: vi.fn(async () => ({
      candidates: [
        {
          id: "c1", kb_id: "test", candidate_type: "node", label: "Meeting",
          frequency: 10, confidence_avg: 0.9,
          confidence_min: 0.85, confidence_max: 0.95,
          source_label: null, target_label: null,
          examples: [], similar_labels: [],
        },
      ],
    })),
    approveGraphSchemaCandidate: vi.fn(async () => ({
      status: "ok", yaml_path: "/tmp/x.yaml", git: {},
    })),
  };
});

vi.mock("@/hooks/useSearch", () => ({
  useSearchableKbs: () => ({ data: { kbs: [{ id: "test", display_name: "test" }] } }),
}));

import { GraphSchemaClient } from "@/components/admin/GraphSchemaClient";

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <GraphSchemaClient />
    </QueryClientProvider>,
  );
}

describe("GraphSchemaClient", () => {
  it("prompts for KB when none selected", () => {
    renderPage();
    expect(screen.getByText(/검토할 KB를 선택/)).toBeInTheDocument();
  });

  it("lists candidates after entering kb_id", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByPlaceholderText("kb_id"), "test");
    await waitFor(() => {
      expect(screen.getByText("Meeting")).toBeInTheDocument();
    });
    expect(screen.getByText("node")).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run tests + commit**

```bash
cd src/apps/web && pnpm exec vitest run tests/unit/graph-schema.test.tsx 2>&1 | tail -20 && cd ../../..
git add \
  src/apps/web/src/app/\(admin\)/admin/graph-schema/ \
  src/apps/web/src/components/admin/GraphSchemaClient.tsx \
  src/apps/web/src/components/admin/AdminSidebar.tsx \
  src/apps/web/tests/unit/graph-schema.test.tsx
git commit -m "feat(web): graph-schema admin page — candidate review + triggers"
```

---

## Task 3: Slack notification module

**Files:** `src/notifications/__init__.py`, `src/notifications/slack.py`,
`tests/unit/test_slack_notifications.py`, `src/config/settings.py`.

- [ ] **Step 1: Extend settings**

In `src/config/settings.py`, before the final `class Settings(...)`:

```python
class NotificationSettings(BaseSettings):
    """Slack + alert thresholds for ops notifications (Phase 5b)."""

    slack_webhook_url: str | None = None
    candidate_pending_threshold: int = 50
    yaml_pr_stale_hours: int = 48
    bootstrap_failure_streak: int = 3

    model_config = SettingsConfigDict(env_prefix="NOTIF_", extra="ignore")
```

Then add to the `class Settings(...)` composition:

```python
notifications: NotificationSettings = NotificationSettings()
```

- [ ] **Step 2: Write failing test**

Create `tests/unit/test_slack_notifications.py`:

```python
"""Slack notification module — send + event helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.notifications.slack import (
    send,
    notify_bootstrap_failure_streak,
    notify_pending_threshold,
    notify_yaml_pr_stale,
)


class TestSend:
    @pytest.mark.asyncio
    async def test_noop_when_webhook_unset(self):
        with patch(
            "src.notifications.slack._get_webhook_url", return_value=None,
        ):
            result = await send("hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_posts_when_webhook_set(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch(
            "src.notifications.slack._get_webhook_url",
            return_value="https://hooks.slack.com/services/X",
        ), patch(
            "src.notifications.slack.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await send("hello")
        assert result is True
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_failures(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=RuntimeError("network down"))

        with patch(
            "src.notifications.slack._get_webhook_url",
            return_value="https://hooks.slack.com/services/X",
        ), patch(
            "src.notifications.slack.httpx.AsyncClient",
            return_value=mock_client,
        ):
            # Must not raise.
            result = await send("hello")
        assert result is False


class TestEventHelpers:
    @pytest.mark.asyncio
    async def test_bootstrap_failure_formats_message(self):
        with patch("src.notifications.slack.send", new=AsyncMock()) as mock_send:
            await notify_bootstrap_failure_streak(kb_id="g-espa", count=3)
        mock_send.assert_awaited_once()
        msg = mock_send.await_args.args[0]
        assert "g-espa" in msg
        assert "3" in msg

    @pytest.mark.asyncio
    async def test_pending_threshold_formats(self):
        with patch("src.notifications.slack.send", new=AsyncMock()) as mock_send:
            await notify_pending_threshold(kb_id="g-espa", pending=67)
        msg = mock_send.await_args.args[0]
        assert "g-espa" in msg
        assert "67" in msg

    @pytest.mark.asyncio
    async def test_yaml_pr_stale_formats(self):
        with patch("src.notifications.slack.send", new=AsyncMock()) as mock_send:
            await notify_yaml_pr_stale(branch="schema/g-espa-20260424", hours=49)
        msg = mock_send.await_args.args[0]
        assert "schema/g-espa-20260424" in msg
```

- [ ] **Step 3: Implement**

Create `src/notifications/__init__.py` (empty).

Create `src/notifications/slack.py`:

```python
"""Slack webhook notification layer for graph-schema alerts (Phase 5b)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def _get_webhook_url() -> str | None:
    """Read webhook URL at call-time — easy to monkeypatch + respects env reload."""
    from src.config import get_settings

    return get_settings().notifications.slack_webhook_url


async def send(text: str) -> bool:
    """Post ``text`` to the configured Slack webhook.

    Returns True if the webhook was invoked successfully. Silent no-op
    when no webhook URL is configured. Network / Slack failures are
    logged and swallowed — ops notifications must never break the
    business flow that emitted them.
    """
    url = _get_webhook_url()
    if not url:
        return False

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, json={"text": text})
            if resp.status_code >= 300:
                logger.warning(
                    "Slack webhook returned %s: %s",
                    resp.status_code, getattr(resp, "text", "")[:200],
                )
                return False
    except (httpx.HTTPError, RuntimeError, OSError) as exc:
        logger.warning("Slack send failed: %s", exc)
        return False
    return True


async def notify_bootstrap_failure_streak(*, kb_id: str, count: int) -> bool:
    return await send(
        f":warning: GraphRAG schema bootstrap failed {count} times in a row "
        f"for `{kb_id}`. On-call please investigate.",
    )


async def notify_pending_threshold(*, kb_id: str, pending: int) -> bool:
    return await send(
        f":inbox_tray: GraphRAG schema — `{kb_id}` has {pending} pending "
        f"candidates awaiting admin review.",
    )


async def notify_yaml_pr_stale(*, branch: str, hours: int) -> bool:
    return await send(
        f":clock3: YAML PR branch `{branch}` unmerged for {hours}h. "
        f"Please review or close.",
    )


__all__ = [
    "notify_bootstrap_failure_streak",
    "notify_pending_threshold",
    "notify_yaml_pr_stale",
    "send",
]
```

- [ ] **Step 4: Run + commit**

```bash
uv run pytest tests/unit/test_slack_notifications.py -v --no-cov
uvx ruff check src/notifications/ tests/unit/test_slack_notifications.py src/config/settings.py
git add src/notifications/ tests/unit/test_slack_notifications.py src/config/settings.py
git commit -m "feat(notifications): Slack webhook — bootstrap/pending/YAML-PR alerts"
```

---

## Task 4: `schema_alerts_sweep` cron + bootstrap failure hookup

**Files:** `src/jobs/schema_alerts.py`, `tests/unit/test_schema_alerts.py`,
modify `src/jobs/schema_bootstrap_jobs.py`, `src/jobs/tasks.py`,
`src/jobs/worker.py`.

- [ ] **Step 1: Test first**

Create `tests/unit/test_schema_alerts.py`:

```python
"""Cron: scan and emit Slack alerts for graph-schema ops events."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs.schema_alerts import run_alerts_sweep


class _FakeSettings:
    class notifications:
        candidate_pending_threshold = 5
        bootstrap_failure_streak = 3
        yaml_pr_stale_hours = 48


class TestRunAlertsSweep:
    @pytest.mark.asyncio
    async def test_pending_threshold_fires(self):
        candidate_repo = MagicMock()
        candidate_repo.count_pending_by_kb = AsyncMock(
            return_value=[("g-espa", 7), ("partner", 1)],
        )
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(return_value={})

        with patch(
            "src.jobs.schema_alerts.get_settings", return_value=_FakeSettings(),
        ), patch(
            "src.jobs.schema_alerts.notify_pending_threshold",
            new=AsyncMock(),
        ) as pending, patch(
            "src.jobs.schema_alerts.notify_bootstrap_failure_streak",
            new=AsyncMock(),
        ) as streak:
            await run_alerts_sweep(
                candidate_repo=candidate_repo, run_repo=run_repo,
            )
        pending.assert_awaited_once()
        kwargs = pending.await_args.kwargs
        assert kwargs["kb_id"] == "g-espa"
        assert kwargs["pending"] == 7
        streak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_streak_fires(self):
        candidate_repo = MagicMock()
        candidate_repo.count_pending_by_kb = AsyncMock(return_value=[])
        run_repo = MagicMock()
        run_repo.recent_failure_streak = AsyncMock(
            return_value={"partner": 3, "g-espa": 1},
        )

        with patch(
            "src.jobs.schema_alerts.get_settings", return_value=_FakeSettings(),
        ), patch(
            "src.jobs.schema_alerts.notify_bootstrap_failure_streak",
            new=AsyncMock(),
        ) as streak:
            await run_alerts_sweep(
                candidate_repo=candidate_repo, run_repo=run_repo,
            )
        streak.assert_awaited_once()
        kwargs = streak.await_args.kwargs
        assert kwargs["kb_id"] == "partner"
        assert kwargs["count"] == 3
```

- [ ] **Step 2: Repo methods needed**

Check existing repos — `SchemaCandidateRepo.count_pending_by_kb` and
`BootstrapRunRepo.recent_failure_streak` may not exist yet. Add thin
implementations.

In `src/stores/postgres/repositories/schema_candidate_repo.py`, append:

```python
    async def count_pending_by_kb(self) -> list[tuple[str, int]]:
        """Return [(kb_id, pending_count)] grouped by kb_id."""
        from sqlalchemy import func, select

        from src.stores.postgres.models import SchemaCandidateModel

        async with self._session_maker() as session:
            stmt = select(
                SchemaCandidateModel.kb_id,
                func.count(SchemaCandidateModel.id),
            ).where(
                SchemaCandidateModel.status == "pending",
            ).group_by(SchemaCandidateModel.kb_id)
            rows = await session.execute(stmt)
            return [(r[0], r[1]) for r in rows.all()]
```

In `src/stores/postgres/repositories/bootstrap_run_repo.py`, append:

```python
    async def recent_failure_streak(
        self, *, window_hours: int = 24,
    ) -> dict[str, int]:
        """Return {kb_id: consecutive_recent_failures} for the given window.

        Counts only the trailing run of failures — a success in between
        resets the streak for that kb_id.
        """
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select

        from src.stores.postgres.models import BootstrapRunModel

        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        async with self._session_maker() as session:
            stmt = select(
                BootstrapRunModel.kb_id, BootstrapRunModel.status,
                BootstrapRunModel.completed_at,
            ).where(
                BootstrapRunModel.completed_at >= cutoff,
            ).order_by(
                BootstrapRunModel.kb_id,
                BootstrapRunModel.completed_at.desc(),
            )
            rows = (await session.execute(stmt)).all()

        streak: dict[str, int] = {}
        for kb_id, status, _ in rows:
            if streak.get(kb_id) is None:
                # First (most recent) row for this kb_id.
                streak[kb_id] = 1 if status == "failed" else 0
                continue
            if streak[kb_id] == 0:
                continue  # Already hit a non-failure; don't grow.
            if status == "failed":
                streak[kb_id] += 1
            else:
                # Mark the streak as broken so later older failures don't count.
                streak[kb_id] = -abs(streak[kb_id])  # negative sentinel
        # Clean negatives → 0.
        return {kb: max(v, 0) for kb, v in streak.items()}
```

- [ ] **Step 3: Implement cron task**

Create `src/jobs/schema_alerts.py`:

```python
"""arq cron: sweep graph-schema ops thresholds and emit Slack alerts.

Spec §5.6 (Ops — alerts). Runs every 30 minutes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import get_settings
from src.notifications.slack import (
    notify_bootstrap_failure_streak,
    notify_pending_threshold,
)

logger = logging.getLogger(__name__)


async def run_alerts_sweep(*, candidate_repo: Any, run_repo: Any) -> None:
    """Core sweep — dependency injected so it's unit-testable."""
    settings = get_settings()
    n = settings.notifications

    try:
        pending_by_kb = await candidate_repo.count_pending_by_kb()
    except Exception:  # noqa: BLE001
        logger.exception("alerts_sweep: count_pending_by_kb failed")
        pending_by_kb = []

    for kb_id, count in pending_by_kb:
        if count >= n.candidate_pending_threshold:
            await notify_pending_threshold(kb_id=kb_id, pending=count)

    try:
        streaks = await run_repo.recent_failure_streak()
    except Exception:  # noqa: BLE001
        logger.exception("alerts_sweep: recent_failure_streak failed")
        streaks = {}

    for kb_id, streak in streaks.items():
        if streak >= n.bootstrap_failure_streak:
            await notify_bootstrap_failure_streak(kb_id=kb_id, count=streak)


async def schema_alerts_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """arq cron entrypoint. Resolves repos from app_state."""
    from src.stores.postgres.repositories.bootstrap_run_repo import (
        BootstrapRunRepo,
    )
    from src.stores.postgres.repositories.schema_candidate_repo import (
        SchemaCandidateRepo,
    )

    app = ctx.get("app_state")
    if app is None or not hasattr(app, "session_maker"):
        logger.warning("schema_alerts_sweep: app_state missing — skipping")
        return {"status": "skipped"}

    await run_alerts_sweep(
        candidate_repo=SchemaCandidateRepo(app.session_maker),
        run_repo=BootstrapRunRepo(app.session_maker),
    )
    return {"status": "ok"}


__all__ = ["run_alerts_sweep", "schema_alerts_sweep"]
```

- [ ] **Step 4: Register task + cron**

In `src/jobs/tasks.py`:

```python
from src.jobs.schema_alerts import schema_alerts_sweep
# ...
REGISTERED_TASKS = [
    # ...existing
    schema_alerts_sweep,
]
```

In `src/jobs/worker.py`, append a cron entry — look for the existing
`schema_bootstrap_cleanup` cron (03:05 UTC pattern) and add:

```python
cron(schema_alerts_sweep, minute={0, 30}),
```

(every half hour.) Import `schema_alerts_sweep` at the top of the file.

- [ ] **Step 5: Run + commit**

```bash
uv run pytest \
  tests/unit/test_schema_alerts.py \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  -v --no-cov
uvx ruff check src/jobs/schema_alerts.py tests/unit/test_schema_alerts.py \
  src/stores/postgres/repositories/schema_candidate_repo.py \
  src/stores/postgres/repositories/bootstrap_run_repo.py \
  src/jobs/tasks.py src/jobs/worker.py
git add src/jobs/schema_alerts.py tests/unit/test_schema_alerts.py \
  src/stores/postgres/repositories/schema_candidate_repo.py \
  src/stores/postgres/repositories/bootstrap_run_repo.py \
  src/jobs/tasks.py src/jobs/worker.py
git commit -m "feat(jobs): schema_alerts_sweep cron — Slack pending/failure alerts"
```

---

## Task 5: Integration test — kb_onboarding

**File:** `tests/integration/test_kb_onboarding.py`.

Full flow: bootstrap over a stubbed LLM → verify candidates persisted →
approve via API helpers → verify YAML updated + `decide` called.
No real services — uses in-memory fakes for LLM + DocSampler, MagicMock
session_maker, tmp_path for YAML dir.

- [ ] **Step 1: Write test**

Create `tests/integration/__init__.py` (empty).

Create `tests/integration/test_kb_onboarding.py`:

```python
"""End-to-end-ish: bootstrap → candidates → approve → YAML updated."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import yaml

from src.api.routes.graph_schema_helpers import merge_label_into_yaml
from src.pipelines.graphrag.schema_bootstrap import (
    BootstrapConfig,
    SchemaBootstrapper,
)


class _FakeLLM:
    """Deterministic LLM stub — returns two candidate labels for any prompt."""

    async def generate(self, prompt: str) -> str:
        return (
            '{"candidate_nodes": [\n'
            '  {"label": "Ticket", "confidence": 0.91, '
            '"frequency": 5, "examples": ["sample ticket"]}\n'
            '], "candidate_relationships": [\n'
            '  {"label": "ASSIGNED_TO", "confidence": 0.88, '
            '"frequency": 3, "source_label": "Person", '
            '"target_label": "Ticket", "examples": ["assigned"]}\n'
            ']}'
        )


class _FakeDocSampler:
    """Returns a fixed set of sample docs."""

    async def sample(self, *, kb_id: str, n: int):
        return [
            {"doc_id": f"d{i}", "content": f"doc {i}", "source_type": "confluence"}
            for i in range(n)
        ]


class _StubRepo:
    """Capturing stub — records upsert calls for assertions."""

    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.decided: list[dict] = []

    async def upsert(self, **kw) -> None:
        self.upserts.append(kw)

    async def decide(self, **kw) -> None:
        self.decided.append(kw)


class _StubRunRepo:
    def __init__(self) -> None:
        self.created = None
        self.completed = None

    async def create(self, **kw) -> None:
        self.created = kw

    async def complete(self, **kw) -> None:
        self.completed = kw

    async def has_running(self, kb_id: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_kb_onboarding_flow(tmp_path, monkeypatch):
    # 1. Bootstrap: sample docs → LLM → candidates persisted
    candidate_repo = _StubRepo()
    run_repo = _StubRunRepo()
    cfg = BootstrapConfig(kb_id="newkb", sample_size=3)
    b = SchemaBootstrapper(
        llm=_FakeLLM(),
        candidate_repo=candidate_repo,
        run_repo=run_repo,
        doc_sampler=_FakeDocSampler(),
        config=cfg,
    )
    await b.run(run_id=uuid4(), trigger="test", triggered_by_user="sys")

    assert run_repo.created is not None
    assert run_repo.completed is not None
    assert run_repo.completed["status"] == "completed"
    # 1 candidate_node + 1 candidate_relationship = 2 upserts
    assert len(candidate_repo.upserts) == 2
    labels = {u["label"] for u in candidate_repo.upserts}
    assert labels == {"Ticket", "ASSIGNED_TO"}

    # 2. Admin approves both — merge into YAML
    schema_dir = tmp_path / "graph_schemas"
    schema_dir.mkdir()
    monkeypatch.setattr(
        "src.api.routes.graph_schema_helpers._SCHEMA_DIR", schema_dir,
    )

    for u in candidate_repo.upserts:
        merge_label_into_yaml(
            kb_id=u["kb_id"],
            candidate_type=u["candidate_type"],
            label=u["label"],
            approved_by="admin@e2e",
        )

    # 3. Verify YAML now contains both labels.
    data = yaml.safe_load((schema_dir / "newkb.yaml").read_text())
    assert "Ticket" in data["nodes"]
    assert "ASSIGNED_TO" in data["relationships"]
    assert data["version"] == 2  # 0 → 1 (node) → 2 (rel)
    approved = data["_metadata"]["approved_candidates"]
    assert {e["label"] for e in approved} == {"Ticket", "ASSIGNED_TO"}
```

- [ ] **Step 2: Run + commit**

```bash
uv run pytest tests/integration/test_kb_onboarding.py -v --no-cov
uvx ruff check tests/integration/
git add tests/integration/
git commit -m "test(integration): kb_onboarding — bootstrap → approve → YAML flow"
```

---

## Task 6: Regression + web test sweep

- [ ] Run full Phase 1-5 + new Phase 5b Python regression.
- [ ] Run the web vitest suite.

```bash
uv run pytest \
  tests/unit/test_reextract_job_repo.py \
  tests/unit/test_schema_reextract_job.py \
  tests/unit/test_graph_schema_cli.py \
  tests/unit/test_graph_schema_routes.py \
  tests/unit/test_graph_schema_helpers.py \
  tests/unit/test_schema_candidate_repo.py \
  tests/unit/test_bootstrap_run_repo.py \
  tests/unit/test_schema_bootstrap.py \
  tests/unit/test_schema_discovery_prompt.py \
  tests/unit/test_schema_resolver.py \
  tests/unit/test_schema_types.py \
  tests/unit/test_dynamic_schema.py \
  tests/unit/test_extractor_schema_integration.py \
  tests/unit/test_graphrag_prompts_facade.py \
  tests/unit/test_source_defaults.py \
  tests/unit/test_schema_migration.py \
  tests/unit/test_slack_notifications.py \
  tests/unit/test_schema_alerts.py \
  tests/integration/test_kb_onboarding.py \
  -q --no-cov 2>&1 | tail -3

cd src/apps/web && pnpm exec vitest run 2>&1 | tail -5 && cd ../../..
```

---

## Spec Coverage

| Spec | Task |
|---|---|
| §5.2 Admin review UI | 2 |
| §5.6 Slack alerts (bootstrap streak, pending threshold) | 3, 4 |
| §5.6 Ops dashboard | 2 (Next.js page is the dashboard) |
| §5.7 First-time onboarding flow | 5 (integration test) |

Deferred (Phase 6 / post): Prometheus metrics wiring, email notifications,
realtime schema evolution, Streamlit mirror page (Next.js is primary).
