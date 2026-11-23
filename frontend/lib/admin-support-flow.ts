import { validSupportScope, type ReportRecord, type SupportScope, type SupportAction } from "./admin-reports";
import { prepareSupportRequest, submitSupportAction, supportWriteFailure, type SupportRequest, type SupportResult, type SupportFailure } from "./admin-support-write";

type Intent = { request: SupportRequest; scope: SupportScope };
export type SupportFlowState = { phase: "idle" } | { phase: "blocked"; message: string }
  | (Intent & { phase: "submitting"; rechecks: number })
  | (Intent & { phase: "uncertain"; message: string; rechecks: number })
  | (Intent & { phase: "stopped"; failure: SupportFailure })
  | { phase: "succeeded"; result: SupportResult };
export const MAX_SUPPORT_RECHECKS = 3;
type Item = Pick<ReportRecord, "id" | "recordId" | "recordType">;
export type SupportFlow = {
  item: Item; state: () => SupportFlowState; subscribe: (listener: () => void) => () => void;
  execute: (report: ReportRecord, action: SupportAction, text: string) => Promise<void>;
  recheck: () => Promise<void>; release: () => void;
};
type Dependencies = { submit: typeof submitSupportAction; newRequestId: () => string };
const defaults: Dependencies = { submit: submitSupportAction, newRequestId: () => crypto.randomUUID() };
const listeners = new Set<() => void>();
let completedVersion = 0;
export const supportCompletedVersion = () => completedVersion;
export const subscribeSupportChanges = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const pending = new Map<SupportFlow, SupportFlowState>();
const notices = new Set<SupportFlow>();
let pendingSnapshot: readonly SupportFlow[] = [];
let noticeSnapshot: readonly SupportFlow[] = [];
const empty: readonly SupportFlow[] = [];
export const pendingSupportFlows = () => pendingSnapshot;
export const supportNotices = () => noticeSnapshot;
export const serverPendingSupportFlows = () => empty;
let guardedWindow: Window | null = null;
const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
function publish(flow: SupportFlow, state: SupportFlowState) {
  if (state.phase === "submitting" || state.phase === "uncertain") pending.set(flow, state); else pending.delete(flow);
  if (state.phase === "idle" || state.phase === "blocked") notices.delete(flow); else notices.add(flow);
  noticeSnapshot = notices.size ? [...notices] : empty;
  pendingSnapshot = pending.size ? [...pending.keys()] : empty;
  if (pending.size && !guardedWindow && typeof window !== "undefined") { guardedWindow = window; window.addEventListener("beforeunload", warn); }
  else if (!pending.size && guardedWindow) { guardedWindow.removeEventListener("beforeunload", warn); guardedWindow = null; }
  if (state.phase === "succeeded" || state.phase === "stopped") completedVersion++;
  listeners.forEach(listener => listener());
}

// Memory only. Detail refreshes, filter changes and modal dismissal cannot lose an
// unconfirmed request or replace its UUID. No background retries or browser storage.
export function createSupportFlow(item: Item, deps: Dependencies = defaults): SupportFlow {
  let state: SupportFlowState = { phase: "idle" };
  const subscribers = new Set<() => void>();
  let flow: SupportFlow;
  const set = (next: SupportFlowState) => { state = next; publish(flow, state); subscribers.forEach(listener => listener()); };
  async function send(request: SupportRequest, scope: SupportScope, rechecks: number) {
    set({ phase: "submitting", request, scope, rechecks });
    try { set({ phase: "succeeded", result: await deps.submit(item, request, scope) }); }
    catch (error) {
      const failure = supportWriteFailure(error);
      if (rechecks > 0 && failure.reload) completedVersion++;
      if (failure.retrySameRequest || rechecks > 0) set({ phase: "uncertain", request, scope, rechecks,
        message: rechecks > 0 && !failure.retrySameRequest ? `${failure.message} 이전 요청의 처리 여부는 아직 확인되지 않았습니다.` : failure.message });
      else set({ phase: "stopped", request, scope, failure });
    }
  }
  flow = { item,
    state: () => state,
    subscribe: listener => { subscribers.add(listener); return () => { subscribers.delete(listener); }; },
    async execute(report, action, text) {
      if (state.phase === "submitting" || state.phase === "uncertain") return;
      if (report.id !== item.id || report.recordType !== item.recordType || report.recordId !== item.recordId || !validSupportScope(report.actions.scope)) {
        set({ phase: "blocked", message: "상세 정보와 처리 권한을 다시 확인해 주세요." }); return;
      }
      let request: SupportRequest;
      try { request = Object.freeze(prepareSupportRequest(report, action, text, deps.newRequestId())); }
      catch (error) { set({ phase: "blocked", message: error instanceof Error ? error.message : "처리 내용을 확인해 주세요." }); return; }
      const scope = Object.freeze({ ...report.actions.scope });
      await send(request, scope, 0);
    },
    async recheck() {
      const current = state;
      if (current.phase !== "uncertain" || current.rechecks >= MAX_SUPPORT_RECHECKS) return;
      await send(current.request, current.scope, current.rechecks + 1);
    },
    release() { if (state.phase !== "submitting" && state.phase !== "uncertain") set({ phase: "idle" }); },
  };
  return flow;
}
const flows = new Map<string, SupportFlow>();
export function supportFlowFor(item: Item) {
  let flow = flows.get(item.id);
  if (!flow) { flow = createSupportFlow({ id: item.id, recordId: item.recordId, recordType: item.recordType }); flows.set(item.id, flow); }
  return flow;
}
