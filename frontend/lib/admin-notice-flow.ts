import { prepareNoticeRequest, submitNoticeRequest, noticeWriteFailure, type NoticeRequest, type NoticeResult, type NoticeWriteFailure } from "./admin-notice-write";
import { validNoticeScope, type NoticeScope, type NoticeAction, type NoticeCapability, type NoticeDraft, type NoticeRecord } from "./admin-notices";

type Intent = { id: string | null; request: NoticeRequest; scope: NoticeScope };
export type NoticeFlowState = { phase: "idle" } | { phase: "blocked"; message: string }
  | (Intent & { phase: "submitting"; rechecks: number }) | (Intent & { phase: "uncertain"; message: string; rechecks: number })
  | (Intent & { phase: "stopped"; failure: NoticeWriteFailure }) | (Intent & { phase: "succeeded"; result: NoticeResult });
export type NoticeFlow = { state: () => NoticeFlowState; subscribe: (listener: () => void) => () => void;
  execute: (item: NoticeRecord | null, action: NoticeAction, draft: NoticeDraft, capabilities?: NoticeCapability) => Promise<void>;
  recheck: () => Promise<void>; release: () => void };
export const MAX_NOTICE_RECHECKS = 3;
let version = 0;
const listeners = new Set<() => void>(), active = new Set<NoticeFlow>();
const empty: readonly NoticeFlow[] = [];
let snapshot: readonly NoticeFlow[] = empty, guardedWindow: Window | null = null;
const warning = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
export const noticeCompletedVersion = () => version;
export const noticeFlows = () => snapshot;
export const serverNoticeFlows = () => empty;
export const subscribeNoticeChanges = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
function publish(flow: NoticeFlow, state: NoticeFlowState) {
  if (["idle", "blocked"].includes(state.phase)) active.delete(flow); else active.add(flow);
  snapshot = active.size ? [...active] : empty;
  const busy = snapshot.some(item => ["submitting", "uncertain"].includes(item.state().phase));
  if (busy && !guardedWindow && typeof window !== "undefined") { guardedWindow = window; window.addEventListener("beforeunload", warning); }
  else if (!busy && guardedWindow) { guardedWindow.removeEventListener("beforeunload", warning); guardedWindow = null; }
  if (state.phase === "succeeded" || state.phase === "stopped") version++;
  listeners.forEach(listener => listener());
}
export function createNoticeFlow(deps = { submit: submitNoticeRequest, newRequestId: () => crypto.randomUUID() }): NoticeFlow {
  let state: NoticeFlowState = { phase: "idle" }, flow: NoticeFlow;
  const subscriptions = new Set<() => void>();
  const set = (next: NoticeFlowState) => { state = next; publish(flow, next); subscriptions.forEach(listener => listener()); };
  async function send(intent: Intent, rechecks: number) {
    set({ ...intent, phase: "submitting", rechecks });
    try { set({ ...intent, phase: "succeeded", result: await deps.submit(intent.id, intent.request, intent.scope) }); }
    catch (error) {
      const failure = noticeWriteFailure(error);
      if (failure.uncertain || rechecks > 0) set({ ...intent, phase: "uncertain", rechecks, message: failure.uncertain ? failure.message : `${failure.message} 이전 요청의 처리 여부는 아직 확인되지 않았습니다.` });
      else set({ ...intent, phase: "stopped", failure });
    }
  }
  flow = { state: () => state, subscribe: listener => { subscriptions.add(listener); return () => { subscriptions.delete(listener); }; },
    async execute(item, action, draft, capabilities) {
      if (state.phase === "submitting" || state.phase === "uncertain" || state.phase === "succeeded") return;
      const scope = (item?.actions ?? capabilities)?.scope;
      if (!validNoticeScope(scope)) { set({ phase: "blocked", message: "처리 권한을 다시 조회해 주세요." }); return; }
      let request: NoticeRequest;
      try { request = Object.freeze(prepareNoticeRequest(item, action, draft, capabilities, deps.newRequestId())); }
      catch (error) { set({ phase: "blocked", message: error instanceof Error ? error.message : "처리 내용을 확인해 주세요." }); return; }
      await send({ id: item?.id ?? null, request, scope: Object.freeze({ ...scope }) }, 0);
    },
    async recheck() { const previous = state; if (previous.phase === "uncertain" && previous.rechecks < MAX_NOTICE_RECHECKS) await send(previous, previous.rechecks + 1); },
    release() { if (state.phase !== "submitting" && state.phase !== "uncertain") set({ phase: "idle" }); },
  };
  return flow;
}
const flows = new Map<string, NoticeFlow>();
export function noticeFlowFor(id: string | null, actor = "unknown") {
  const key = `${actor}:${id ?? "new"}`;
  let flow = flows.get(key); if (!flow) { flow = createNoticeFlow(); flows.set(key, flow); } return flow;
}
