"use client";

import { FileText } from "lucide-react";

import { Tabs } from "@/components/ui";

import { MyDocumentsTab } from "./MyDocumentsTab";
import { NotificationsTab } from "./NotificationsTab";
import { PendingTab } from "./PendingTab";

export function MyDocumentsPage({ userId }: { userId: string }) {
  return (
    <section className="mx-auto w-full max-w-4xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="flex items-center gap-2 text-2xl font-semibold leading-snug text-fg-default">
          <FileText aria-hidden size={22} strokeWidth={1.75} className="text-accent-default" />
          <span>내 담당 문서</span>
        </h1>
        <p className="text-sm text-fg-muted">
          담당 문서 / 대기 작업 / 알림을 한 곳에서 확인합니다.
        </p>
      </header>
      <Tabs
        items={[
          {
            id: "documents",
            label: "담당 문서",
            content: <MyDocumentsTab userId={userId} />,
          },
          { id: "pending", label: "대기 작업", content: <PendingTab /> },
          { id: "notifications", label: "알림", content: <NotificationsTab /> },
        ]}
      />
    </section>
  );
}
