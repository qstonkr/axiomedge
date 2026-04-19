import { ErrorsClient } from "@/components/admin/ErrorsClient";

export const metadata = { title: "오류 신고 · axiomedge admin" };

export default function AdminErrorsRoute() {
  return <ErrorsClient />;
}
