import type { ReactNode } from "react";

import { cn } from "@/components/ui/cn";

/**
 * Shared shell for non-chat hub pages (`/my-*`, `/security`).
 *
 * Existed-as-pattern across MyDocumentsPage / MyFeedbackPage /
 * MyActivitiesPage but each page re-declared the same className soup, and
 * `/my-knowledge` + `/security` drifted to a narrower / smaller-h1 variant.
 * This component locks the shape:
 *
 *   - default max-width: 4xl (~896px) — comfortable reading + form column
 *   - heading: h1 text-2xl semibold
 *   - vertical rhythm: space-y-6 between header and the page body
 *   - horizontal padding: 6, vertical padding: 8
 *
 * Only `/chat` opts out (it owns its own 3-pane layout).
 */
export function PageShell({
  title,
  description,
  icon,
  maxWidth = "4xl",
  children,
  className,
  headerExtra,
}: {
  title: string;
  description?: ReactNode;
  /** Optional emoji or icon prefix shown next to the title. */
  icon?: ReactNode;
  /** Tailwind max-w-* token. Default `4xl` (896px). Bump to `5xl` for
   * dense table pages, `3xl` for narrow read-only pages. */
  maxWidth?: "3xl" | "4xl" | "5xl";
  children: ReactNode;
  className?: string;
  /** Right-aligned slot in the header row (e.g. action buttons). */
  headerExtra?: ReactNode;
}) {
  const widthClass = {
    "3xl": "max-w-3xl",
    "4xl": "max-w-4xl",
    "5xl": "max-w-5xl",
  }[maxWidth];
  return (
    <section className={cn("mx-auto w-full space-y-6 px-6 py-8", widthClass, className)}>
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold leading-snug text-fg-default">
            {icon && <span className="mr-2" aria-hidden>{icon}</span>}
            {title}
          </h1>
          {description && (
            <p className="text-sm text-fg-muted">{description}</p>
          )}
        </div>
        {headerExtra}
      </header>
      {children}
    </section>
  );
}
