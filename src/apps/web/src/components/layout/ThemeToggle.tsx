"use client";

import { useEffect } from "react";
import { Moon, Sun } from "lucide-react";

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
      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-muted hover:text-fg-default"
    >
      {theme === "dark" ? (
        <Moon size={14} strokeWidth={1.75} aria-hidden />
      ) : (
        <Sun size={14} strokeWidth={1.75} aria-hidden />
      )}
    </button>
  );
}
