"use client";

export function CitationMarker({
  n, onActivate, onDeactivate,
}: {
  n: number;
  onActivate: (n: number) => void;
  onDeactivate?: () => void;
}) {
  return (
    <button
      onClick={() => onActivate(n)}
      onMouseEnter={() => onActivate(n)}
      onMouseLeave={() => onDeactivate?.()}
      className="mx-0.5 inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded bg-bg-emphasis px-1 text-[11px] font-medium text-fg-default hover:bg-fg-default hover:text-bg-default"
    >
      [{n}]
    </button>
  );
}
