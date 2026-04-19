import { Skeleton } from "@/components/ui";

export default function AppLoading() {
  return (
    <section className="mx-auto w-full max-w-4xl space-y-4 px-6 py-8">
      <Skeleton className="h-8 w-1/3" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
    </section>
  );
}
