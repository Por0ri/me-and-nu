"use client";

import type { MyPagePolicyType } from "@/lib/pivot/policy-document";
import { isLocalPolicyReviewUrl } from "@/lib/pivot/policy-review";
import { LocalPolicyReview } from "./local-policy-review";
import { PolicyTextModal } from "./policy-text-modal";

export function MyPagePolicyDialog({ type, demo, onClose }: { type: MyPagePolicyType; demo: boolean; onClose: () => void }) {
  // Explicit local Demo entry only. The server independently enforces its opt-in
  // and environment boundary; changing a query string cannot enable publication.
  if (process.env.NODE_ENV === "development" && demo && typeof window !== "undefined" && isLocalPolicyReviewUrl(window.location.href)) {
    return <LocalPolicyReview type={type} onClose={onClose} />;
  }
  // Service and Demo both show the bundled full texts (lib/pivot/policy-texts.ts).
  return <PolicyTextModal kind={type} onClose={onClose} />;
}
