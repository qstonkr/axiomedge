"use client";

import { useEffect } from "react";

/**
 * Bind ``Escape`` key on window to a callback while ``enabled`` is true.
 * Used by modal dialogs (Day 9 a11y polish).
 */
export function useEscape(enabled: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!enabled) return;
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onEscape();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled, onEscape]);
}
