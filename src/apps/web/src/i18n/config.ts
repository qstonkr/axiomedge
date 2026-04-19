/**
 * i18n config — Korean primary, English scaffold for B-2.
 *
 * This pass uses next-intl's "static" mode (NextIntlClientProvider with
 * messages prop) without locale-prefixed routing. Routing-based switch
 * (e.g. /en/chat) lands in B-2 along with admin pages.
 */

import koMessages from "./messages/ko.json";
import enMessages from "./messages/en.json";

export const LOCALES = ["ko", "en"] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "ko";

export const MESSAGES: Record<Locale, Record<string, unknown>> = {
  ko: koMessages,
  en: enMessages,
};

export function getMessages(locale: Locale = DEFAULT_LOCALE) {
  return MESSAGES[locale] ?? MESSAGES[DEFAULT_LOCALE];
}
