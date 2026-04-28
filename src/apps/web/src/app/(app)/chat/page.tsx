import { redirect } from "next/navigation";

import { ChatPage } from "@/components/chat/ChatPage";
import { getSession } from "@/lib/auth/session";

export const metadata = { title: "지식 검색 · axiomedge" };

export default async function ChatRoute() {
  const session = await getSession();
  if (!session) redirect("/login");
  return <ChatPage userEmail={session.email} />;
}
