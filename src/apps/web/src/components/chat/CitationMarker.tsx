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
      type="button"
      onClick={() => onActivate(n)}
      onMouseEnter={() => onActivate(n)}
      onMouseLeave={() => onDeactivate?.()}
      onFocus={() => onActivate(n)}
      onBlur={() => onDeactivate?.()}
      // accent-subtle bg + accent-emphasis text — pops against body copy
      // without being shouty. Hover/focus inverts to accent-default.
      className="mx-0.5 inline-flex h-5 min-w-[1.5rem] items-center justify-center rounded border border-accent-default/30 bg-accent-subtle px-1 text-[11px] font-semibold text-accent-emphasis hover:border-accent-default hover:bg-accent-default hover:text-fg-onAccent focus-visible:outline-2 focus-visible:outline-accent-default"
    >
      [{n}]
    </button>
  );
}
