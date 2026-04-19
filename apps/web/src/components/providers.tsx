"use client";

import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";

import { QueryProvider } from "@/lib/query-client";
import { ToastProvider } from "@/components/ui/Toast";
import { DEFAULT_LOCALE, getMessages, type Locale } from "@/i18n/config";

/** All client-side providers needed by the (app) tree. */
export function AppProviders({
  children,
  locale = DEFAULT_LOCALE,
}: {
  children: ReactNode;
  locale?: Locale;
}) {
  return (
    <NextIntlClientProvider locale={locale} messages={getMessages(locale)}>
      <QueryProvider>
        <ToastProvider>{children}</ToastProvider>
      </QueryProvider>
    </NextIntlClientProvider>
  );
}
