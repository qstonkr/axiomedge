/**
 * Tiny `cn` — joins class names, drops falsy values. We avoid the clsx
 * dependency to keep the UI primitives zero-runtime-cost.
 */
export function cn(
  ...parts: Array<string | false | null | undefined>
): string {
  return parts.filter(Boolean).join(" ");
}
