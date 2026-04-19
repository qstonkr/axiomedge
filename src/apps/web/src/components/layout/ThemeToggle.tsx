"use client";

import { useEffect } from "react";

import { useThemeStore } from "@/store/theme";

export function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme);
  const toggle = useThemeStore((s) => s.toggle);
  const hydrate = useThemeStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`${next === "dark" ? "다크" : "라이트"} 모드로 전환`}
      title={`${next === "dark" ? "다크" : "라이트"} 모드로 전환`}
      className="rounded-md px-2 py-1 text-xs text-fg-muted transition-colors hover:bg-bg-muted hover:text-fg-default"
    >
      {theme === "dark" ? "🌙" : "☀️"}
    </button>
  );
}
