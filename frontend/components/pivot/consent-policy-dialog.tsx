"use client";

import type { Policy } from "@/lib/api";
import { policyLabel } from "@/lib/pivot/consent";
import { isSamplePolicy, policyContent } from "@/lib/pivot/policy-document";
import { PolicyDialog, PolicyDocumentContent } from "./policy-dialog";
import { PolicyTextModal, policyTextModalKind } from "./policy-text-modal";
import styles from "./pivot.module.css";

/** Terms and privacy rows open the bundled full texts; other consent items keep the server text dialog. */
export function ConsentPolicyDialog({ policy, onClose }: { policy: Policy; onClose: () => void }) {
  const kind = policyTextModalKind(policy.type);
  if (kind) return <PolicyTextModal kind={kind} onClose={onClose} />;
  return <PolicyDialog title={policyLabel(policy)} onClose={onClose}>
    <p className={styles.policyVersion}>버전 {policy.policyVersion} · {policy.required ? "필수" : "선택"}</p>
    {isSamplePolicy(policy) ? <p role="status">테스트 환경 안내이며, 실제 서비스의 법적 정책 문서가 아니에요.</p> : null}
    <PolicyDocumentContent title={policyLabel(policy)} content={policyContent(policy.text)} />
  </PolicyDialog>;
}
