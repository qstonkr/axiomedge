import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "axiomedge",
  description:
    "Korean GraphRAG knowledge management — search, ask, contribute.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // The Pretendard fallback chain lives in globals.css `--font-sans`.
  // We don't preload a remote webfont here — system Korean fonts cover the
  // first paint, and Pretendard is loaded from CDN only when the user has
  // it installed locally. Day 9 may revisit (self-hosted Pretendard).
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
