import { TracesClient } from "@/components/admin/TracesClient";

export const metadata = { title: "Agent Trace · axiomedge admin" };

export default function AdminTracesRoute() {
  return <TracesClient />;
}
