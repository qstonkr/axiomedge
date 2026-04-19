"use client";

import { Tabs } from "@/components/ui";

import { ErrorReportTab } from "./ErrorReportTab";
import { FeedbackTab } from "./FeedbackTab";

export function MyFeedbackPage() {
  return (
    <section className="mx-auto w-full max-w-4xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold leading-snug text-fg-default">
          📝 피드백 / 오류 신고
        </h1>
        <p className="text-sm text-fg-muted">
          서비스에 남길 의견이나 문서 오류를 알려주세요.
        </p>
      </header>
      <Tabs
        items={[
          { id: "feedback", label: "피드백", content: <FeedbackTab /> },
          { id: "error", label: "오류 신고", content: <ErrorReportTab /> },
        ]}
      />
    </section>
  );
}
