import type { AdminContentDetail } from "./admin-content";
import {
  prepareReviewRequest, reviewAvailability, reviewFailure, submitAdminContentReview,
  type AdminContentReviewRequest, type AdminContentReviewResult, type ReviewAction, type ReviewFailure,
} from "./admin-content-review";
import { reviewWriteGate, type ReviewWriteGate } from "./admin-review-write-gate";

// One review action per content, from confirmation to a server-proven result. The flow is
// kept in a module registry (not component state), so a detail re-read, modal close or panel
// remount during a request never loses the request object or turns an unknown outcome into
// a failure. Nothing is ever sent automatically: rechecks are manual, limited and reuse the
// identical frozen request (same requestId and six fields).
export const MAX_RECHECKS = 3;

export type ReviewFlowState =
  | { phase: "idle" }
  | { phase: "blocked"; message: string } // Nothing was sent.
  | { phase: "submitting"; request: AdminContentReviewRequest; recheck: boolean }
  | { phase: "succeeded"; request: AdminContentReviewRequest; result: AdminContentReviewResult } // HTTP 200 only.
  | { phase: "uncertain"; request: AdminContentReviewRequest; message: string; rechecks: number } // Outcome unknown.
  | { phase: "stopped"; request: AdminContentReviewRequest; failure: ReviewFailure }; // Do not resend this request.

export type ReviewFlowDeps = {
  submit: (contentId: number, request: AdminContentReviewRequest) => Promise<AdminContentReviewResult>;
  newRequestId: () => string;
  gate: () => ReviewWriteGate;
};
export type ReviewFlow = {
  readonly contentId: number;
  state: () => ReviewFlowState;
  subscribe: (listener: () => void) => () => void;
  writeGate: () => ReviewWriteGate;
  // The latest detail re-read; while none is attached, a needed re-read waits for the next one.
  setReload: (reload: (() => void) | null) => void;
  execute: (content: AdminContentDetail, action: ReviewAction, rationale: string) => Promise<void>;
  recheck: () => Promise<void>;
  // Leave the current result or unknown request and re-read the detail (no request is sent).
  release: () => void;
};

const defaultDeps: ReviewFlowDeps = {
  submit: submitAdminContentReview,
  newRequestId: () => crypto.randomUUID(),
  gate: () => reviewWriteGate(),
};

export function createReviewFlow(contentId: number, deps: ReviewFlowDeps = defaultDeps): ReviewFlow {
  let state: ReviewFlowState = { phase: "idle" };
  let reload: (() => void) | null = null, reloadPending = false;
  const listeners = new Set<() => void>();
  const set = (next: ReviewFlowState) => { state = next; listeners.forEach(listener => listener()); };
  const requestReload = () => { if (reload) { reloadPending = false; reload(); } else reloadPending = true; };
  const busy = () => state.phase === "submitting" || state.phase === "uncertain";

  async function send(request: AdminContentReviewRequest, rechecks: number) {
    set({ phase: "submitting", request, recheck: rechecks > 0 });
    try {
      const result = await deps.submit(contentId, request);
      set({ phase: "succeeded", request, result });
      requestReload(); // Show the current state from a fresh GET, never from the action alone.
    } catch (error) {
      const failure = reviewFailure(error);
      if (failure.retrySameRequest) set({ phase: "uncertain", request, message: failure.message, rechecks });
      else {
        set({ phase: "stopped", request, failure });
        if (failure.reload) requestReload(); // 409/403/401/404: re-read state and permission, never re-run.
      }
    }
  }

  return {
    contentId,
    state: () => state,
    subscribe: listener => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    writeGate: () => deps.gate(),
    setReload: next => { reload = next; if (next && reloadPending) requestReload(); },
    async execute(content, action, rationale) {
      if (busy()) return; // No second request while one is in flight or unresolved.
      const gate = deps.gate();
      if (!gate.enabled) return set({ phase: "blocked", message: gate.reason ?? "승인·반려 실행이 비활성화되어 있습니다." });
      if (content.contentId !== contentId) return set({ phase: "blocked", message: "콘텐츠를 다시 조회해 주세요." });
      const availability = reviewAvailability(content);
      if (!availability.allowedActions.includes(action)) {
        return set({ phase: "blocked", message: availability.message ?? "현재 이 조치를 실행할 수 없습니다. 콘텐츠를 다시 조회해 주세요." });
      }
      let request: AdminContentReviewRequest;
      try { request = Object.freeze(prepareReviewRequest(content, action, rationale, deps.newRequestId())); }
      catch (error) { return set({ phase: "blocked", message: error instanceof Error ? error.message : "검수 요청을 확인해 주세요." }); }
      await send(request, 0);
    },
    async recheck() {
      const current = state;
      if (current.phase !== "uncertain" || current.rechecks >= MAX_RECHECKS || !deps.gate().enabled) return;
      await send(current.request, current.rechecks + 1);
    },
    release() {
      if (state.phase === "submitting") return;
      const unresolved = state.phase === "uncertain";
      set({ phase: "idle" });
      if (unresolved) requestReload();
    },
  };
}

const flows = new Map<number, ReviewFlow>();
// Production panels always use the default deps, so the code-only write gate applies.
export function reviewFlowFor(contentId: number): ReviewFlow {
  let flow = flows.get(contentId);
  if (!flow) { flow = createReviewFlow(contentId); flows.set(contentId, flow); }
  return flow;
}
