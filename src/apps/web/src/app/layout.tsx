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
        {/* Apply persisted theme BEFORE React hydrates to avoid the
            light-mode flash dark-mode users were seeing on every navigation
            (`store/theme.ts` reads localStorage post-mount). The script
            mirrors the priority order used by useTheme: explicit pref →
            system. Inlined so it runs synchronously during head parse. */}
        <script
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var p=localStorage.getItem('axiomedge.theme');" +
              "var t=p||(window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');" +
              "document.documentElement.dataset.theme=t;}catch(e){}})();",
          }}
        />
      </head>
      <body className="flex min-h-screen flex-col">{children}</body>
    </html>
  );
}
