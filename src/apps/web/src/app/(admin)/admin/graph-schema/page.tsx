import { GraphSchemaClient } from "@/components/admin/GraphSchemaClient";

export const metadata = { title: "그래프 스키마 · axiomedge admin" };

export default function AdminGraphSchemaRoute() {
  return <GraphSchemaClient />;
}
