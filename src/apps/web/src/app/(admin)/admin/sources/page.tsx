import { DataSourcesClient } from "@/components/admin/DataSourcesClient";

export const metadata = { title: "데이터 소스 · axiomedge admin" };

export default function AdminDataSourcesRoute() {
  return <DataSourcesClient />;
}
