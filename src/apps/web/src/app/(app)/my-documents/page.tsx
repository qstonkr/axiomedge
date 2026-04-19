import { redirect } from "next/navigation";

import { MyDocumentsPage } from "@/components/my-documents/MyDocumentsPage";
import { getSession } from "@/lib/auth/session";

export const metadata = { title: "내 담당 문서 · axiomedge" };

export default async function MyDocumentsRoute() {
  const session = await getSession();
  if (!session) redirect("/login");
  return <MyDocumentsPage userId={session.sub} />;
}
