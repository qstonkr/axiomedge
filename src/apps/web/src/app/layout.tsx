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
  return (
    <html lang="ko" className="h-full antialiased">
      <head>
        {/* Pretendard via CDN — Korean-first webfont. The variable build covers
            the full weight range with a single file. Falls back to system
            Korean fonts if the CDN is unreachable (chain in globals.css). */}
        <link
          rel="preload"
          as="style"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
        />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
        />
      </head>
      <body className="flex min-h-screen flex-col">{children}</body>
    </html>
  );
}
