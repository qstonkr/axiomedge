import { JobsClient } from "@/components/admin/JobsClient";

export const metadata = { title: "작업 모니터 · axiomedge admin" };

export default function AdminJobsRoute() {
  return <JobsClient />;
}
