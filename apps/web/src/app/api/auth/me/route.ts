import { NextResponse } from "next/server";

import { getSession } from "@/lib/auth/session";

/**
 * Browser-friendly wrapper around the server-only ``getSession``.
 * Used by client components to refresh the user chip after switch-org etc.
 */
export async function GET() {
  const session = await getSession();
  if (!session) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  return NextResponse.json(session);
}
