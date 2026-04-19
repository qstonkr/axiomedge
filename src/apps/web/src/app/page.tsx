import { redirect } from "next/navigation";

import { getSession } from "@/lib/auth/session";

/**
 * Root — bounce to /chat when logged in, /login otherwise.
 * The previous Day 2 bootstrap placeholder has been replaced now that the
 * full app is wired up.
 */
export default async function Home() {
  const session = await getSession();
  redirect(session ? "/chat" : "/login");
}
