import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui/Toast";

vi.mock("@/lib/api/endpoints", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/endpoints")>(
      "@/lib/api/endpoints",
    );
  return {
    ...actual,
    listGraphSchemaCandidates: vi.fn(async () => ({
      candidates: [
        {
          id: "c1",
          kb_id: "test",
          candidate_type: "node",
          label: "Meeting",
          frequency: 10,
          confidence_avg: 0.9,
          confidence_min: 0.85,
          confidence_max: 0.95,
          source_label: null,
          target_label: null,
          examples: [],
          similar_labels: [],
        },
      ],
    })),
    approveGraphSchemaCandidate: vi.fn(async () => ({
      status: "ok",
      yaml_path: "/tmp/x.yaml",
      git: {},
    })),
  };
});

vi.mock("@/hooks/useSearch", () => ({
  useSearchableKbs: () => ({
    data: [{ id: "test", display_name: "test", status: "active" }],
  }),
}));

import { GraphSchemaClient } from "@/components/admin/GraphSchemaClient";

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <GraphSchemaClient />
      </ToastProvider>
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
