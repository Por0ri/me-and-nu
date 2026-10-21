import { notFound } from "next/navigation";
import { connection } from "next/server";
import { DemoFlow } from "@/components/pivot/demo-flow";
import { canAccessPivotDemo } from "@/lib/pivot/demo-access";

export default async function Page({ searchParams }: { searchParams: Promise<{ view?: string; detailState?: string }> }) {
  // Evaluate server settings per request, never freeze access into a static build.
  await connection();
  if (!canAccessPivotDemo({
    nodeEnv: process.env.NODE_ENV,
    deploymentEnv: process.env.VERCEL_ENV,
    enabled: process.env.PIVOT_DEMO_ENABLED,
  })) notFound();
  const { view, detailState } = await searchParams;
  return <DemoFlow initialHome={view === "home" || view === "detail" || view === "my" || view === "saved"} detailPreview={detailState} />;
}
