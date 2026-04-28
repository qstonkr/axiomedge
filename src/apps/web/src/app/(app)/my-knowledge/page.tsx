import { redirect } from "next/navigation";

import { MyKnowledgePage } from "@/components/my-knowledge/MyKnowledgePage";
import { getSession } from "@/lib/auth/session";

export const metadata = { title: "내 지식 · axiomedge" };

export default async function MyKnowledgeRoute() {
  const session = await getSession();
  if (!session) redirect("/login");
  return <MyKnowledgePage userId={session.sub} />;
}
