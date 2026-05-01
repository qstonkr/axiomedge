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
    // suppressHydrationWarning: <head> 의 인라인 스크립트가 React hydrate 전에
    // ``document.documentElement.dataset.theme`` 를 set 해서 server HTML 과
    // client HTML 사이 ``data-theme`` 속성 불일치가 의도적으로 발생.
    // 이 1-element-level 차이는 우리가 의도한 동작 (theme flash 방지) 이므로
    // suppress. https://nextjs.org/docs/messages/react-hydration-error
    <html lang="ko" className="h-full antialiased" suppressHydrationWarning>
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
