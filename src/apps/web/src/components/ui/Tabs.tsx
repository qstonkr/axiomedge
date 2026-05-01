"use client";

import { useId, useState, type ReactNode, type KeyboardEvent } from "react";

import { cn } from "./cn";

export type TabItem = {
  id: string;
  label: string;
  content: ReactNode;
};

export function Tabs({
  items,
  defaultActiveId,
  className,
}: {
  items: TabItem[];
  defaultActiveId?: string;
  className?: string;
}) {
  const [active, setActive] = useState(defaultActiveId ?? items[0]?.id);
  const baseId = useId();

  function onKey(e: KeyboardEvent<HTMLButtonElement>, idx: number) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    const next = items[(idx + dir + items.length) % items.length];
    setActive(next?.id);
  }

  return (
    <div className={cn("space-y-4", className)}>
      <div
        role="tablist"
        className="flex overflow-x-auto border-b border-border-default scrollbar-thin"
      >
        {items.map((tab, idx) => {
          const selected = tab.id === active;
          const tabId = `${baseId}-${tab.id}-tab`;
          const panelId = `${baseId}-${tab.id}-panel`;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={tabId}
              aria-selected={selected}
              aria-controls={panelId}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(tab.id)}
              onKeyDown={(e) => onKey(e, idx)}
              className={cn(
                "shrink-0 whitespace-nowrap px-4 py-2 text-sm transition-colors",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-default focus-visible:outline-offset-2",
                selected
                  ? "border-b-2 border-accent-default font-medium text-fg-default"
                  : "border-b-2 border-transparent text-fg-muted hover:text-fg-default",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {items.map((tab) => {
        const selected = tab.id === active;
        const tabId = `${baseId}-${tab.id}-tab`;
        const panelId = `${baseId}-${tab.id}-panel`;
        return (
          <div
            key={tab.id}
            role="tabpanel"
            id={panelId}
            aria-labelledby={tabId}
            hidden={!selected}
          >
            {selected ? tab.content : null}
          </div>
        );
      })}
    </div>
  );
}
