import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { canReadLocalPolicyReview } from "@/lib/pivot/policy-review-access";
import { parseTeamPolicyReview } from "@/lib/pivot/policy-review";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const headers = { "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow", "X-Content-Type-Options": "nosniff" };

export async function GET(request: Request) {
  if (!canReadLocalPolicyReview(request, {
    nodeEnv: process.env.NODE_ENV, deploymentEnv: process.env.VERCEL_ENV || process.env.VERCEL, enabled: process.env.PIVOT_POLICY_REVIEW_ENABLED,
  })) return new Response("Not found", { status: 404, headers });
  // The loopback launcher supplies this server-only runtime path. Do not add a
  // literal fallback: static fs paths can be copied by Next's deployment tracer.
  const sourcePath = process.env.PIVOT_POLICY_REVIEW_SOURCE;
  if (!sourcePath) return new Response("Not found", { status: 404, headers });
  try {
    // Local ignored file only: excluded from Git, public/, JS and deployment traces.
    const markdown = (await readFile(sourcePath, "utf8")).replace(/\r\n/g, "\n");
    if (createHash("sha256").update(markdown).digest("hex") !== "dacd5f1a169b7cc6d774671c6e0d2c330bb910c552a6465de8c90bbf8ea987f2") throw new Error("Review source changed");
    parseTeamPolicyReview(markdown);
    return Response.json({ kind: "development-review", markdown }, { headers });
  } catch (error) {
    // A fresh checkout has no private source; leave review unavailable.
    if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") {
      return new Response("Not found", { status: 404, headers });
    }
    return Response.json({ message: "검토 문서를 불러오지 못했어요." }, { status: 503, headers });
  }
}
