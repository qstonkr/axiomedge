import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * Vitest config — component & route-handler unit tests.
 * E2E lives separately in playwright.config.ts.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      // ``server-only`` is a Next.js sentinel that throws if imported from a
      // client bundle. In Vitest (node environment) we replace it with an
      // empty shim so server modules can be exercised in isolation.
      "server-only": path.resolve(__dirname, "tests/shims/server-only.ts"),
    },
  },
});
