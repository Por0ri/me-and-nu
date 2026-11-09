"use client";

import { useEffect, useState } from "react";
import { myPagePolicyTitles, resolvePolicyDocument, type MyPagePolicyType, type PolicyDocumentState } from "@/lib/pivot/policy-document";
import { loadPivotPolicies } from "@/lib/pivot/services";
import { PolicyDialog, PolicyDocumentContent } from "./policy-dialog";
import { isLocalPolicyReviewUrl } from "@/lib/pivot/policy-review";
import { LocalPolicyReview } from "./local-policy-review";
import styles from "./pivot.module.css";

export function MyPagePolicyDialog({ type, demo, onClose }: { type: MyPagePolicyType; demo: boolean; onClose: () => void }) {
  // Explicit local Demo entry only. The server independently enforces its opt-in
  // and environment boundary; changing a query string cannot enable publication.
  if (process.env.NODE_ENV === "development" && demo && typeof window !== "undefined" && isLocalPolicyReviewUrl(window.location.href)) {
    return <LocalPolicyReview type={type} onClose={onClose} />;
  }
  return <PublishedPolicyDialog type={type} demo={demo} onClose={onClose} />;
}

function PublishedPolicyDialog({ type, demo, onClose }: { type: MyPagePolicyType; demo: boolean; onClose: () => void }) {
  const [state, setState] = useState<PolicyDocumentState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    // Demo stays disconnected from account/session APIs and never substitutes fixtures.
    if (demo) return () => controller.abort();
    loadPivotPolicies(controller.signal).then((policies) => resolvePolicyDocument(policies, type)).then(
      (next) => { if (!controller.signal.aborted) setState(next); },
      () => { if (!controller.signal.aborted) setState({ status: "error", message: "정책 문서를 불러오지 못했어요. 연결을 확인한 뒤 다시 시도해주세요." }); },
    );
    return () => controller.abort();
  }, [type, demo, attempt]);
  const title = myPagePolicyTitles[type];
  return <PolicyDialog title={title} onClose={onClose}>
    {demo ? <p role="status">Demo에서는 정책 원문을 제공하지 않아요. 실제 서비스에서 확인해주세요.</p>
      : <div data-policy-state={state.status} aria-busy={state.status === "loading"}>
        {state.status === "loading" ? <p role="status">정책 문서를 불러오고 있어요.</p>
          : state.status === "ready" ? <>
            <p className={styles.policyVersion}>버전 {state.policy.policyVersion}</p>
            <PolicyDocumentContent title={title} content={state.content} />
          </> : <>
            <p role={state.status === "error" ? "alert" : "status"}>{state.message}</p>
            <button type="button" className={styles.textButton} onClick={() => { setState({ status: "loading" }); setAttempt((value) => value + 1); }}>다시 시도</button>
          </>}
      </div>}
  </PolicyDialog>;
}
