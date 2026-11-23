import { ApiError, apiRequest } from "@/lib/api";

export type OwnInquiryItem = {
  inquiryId: string; inquiryType: "service" | "bug"; status: "received" | "answered" | "closed";
  createdAt: string; updatedAt: string; preview: string; answerAvailability: "available" | "none" | "unavailable";
};
export type OwnInquiryPage = { items: OwnInquiryItem[]; page: number; pageSize: number; total: number };
export type OwnInquiryDetail = OwnInquiryItem & { detail: string; answer: string | null; answeredAt: string | null };
export type InquiryHistorySource = {
  list(page: number, signal: AbortSignal): Promise<OwnInquiryPage>;
  detail(id: string, signal: AbortSignal): Promise<OwnInquiryDetail>;
};
const idPattern = /^[1-9][0-9]{0,18}$/;
const timestamp = (value: unknown): value is string => typeof value === "string" && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value));
function invalid(): never { throw new ApiError("문의 내역 응답을 확인할 수 없어요.", 200, "INVALID_RESPONSE"); }
function item(value: unknown): OwnInquiryItem {
  if (!value || typeof value !== "object") return invalid();
  const row = value as OwnInquiryItem;
  if (typeof row.inquiryId !== "string" || !idPattern.test(row.inquiryId) || BigInt(row.inquiryId) > BigInt("9223372036854775807")
      || !["service", "bug"].includes(row.inquiryType) || !["received", "answered", "closed"].includes(row.status)
      || !["available", "none", "unavailable"].includes(row.answerAvailability)
      || !timestamp(row.createdAt) || !timestamp(row.updatedAt) || typeof row.preview !== "string") return invalid();
  // Pick public fields only; internal fields must never become UI state.
  return { inquiryId: row.inquiryId, inquiryType: row.inquiryType, status: row.status, createdAt: row.createdAt,
    updatedAt: row.updatedAt, preview: row.preview, answerAvailability: row.answerAvailability };
}
export async function getOwnInquiries(page: number, signal: AbortSignal, actorUserId?: number): Promise<OwnInquiryPage> {
  if (!Number.isInteger(page) || page < 1 || page > 100_000) return invalid();
  const body = await apiRequest<OwnInquiryPage>(`/api/v1/inquiries?page=${page}&pageSize=10`,
    { signal, headers: actorUserId === undefined ? undefined : { "X-Inquiry-Actor-User-Id": String(actorUserId) } }, 200);
  if (!body || !Array.isArray(body.items) || body.page !== page || body.pageSize !== 10 || !Number.isSafeInteger(body.total) || body.total < 0
      || body.items.length > body.pageSize || body.items.length > body.total) return invalid();
  return { items: body.items.map(item), page, pageSize: 10, total: body.total };
}
export async function getOwnInquiry(id: string, signal: AbortSignal, actorUserId?: number): Promise<OwnInquiryDetail> {
  if (!idPattern.test(id) || BigInt(id) > BigInt("9223372036854775807")) return invalid();
  const body = await apiRequest<OwnInquiryDetail>(`/api/v1/inquiries/${id}`,
    { signal, headers: actorUserId === undefined ? undefined : { "X-Inquiry-Actor-User-Id": String(actorUserId) } }, 200);
  const row = item(body);
  if (row.inquiryId !== id || typeof body.detail !== "string"
      || (row.answerAvailability === "available" ? typeof body.answer !== "string" || !body.answer.trim() || !timestamp(body.answeredAt)
        : body.answer !== null || body.answeredAt !== null)) return invalid();
  return { ...row, detail: body.detail, answer: body.answer, answeredAt: body.answeredAt };
}

export type InquiryHistoryState = {
  phase: "idle" | "loading" | "ready" | "error" | "unavailable"; page: number; total: number;
  items: OwnInquiryItem[]; selectedId: string | null; detail: OwnInquiryDetail | null;
  detailPhase: "idle" | "loading" | "ready" | "error"; message: string; detailMessage: string;
};
export const initialInquiryHistory = (): InquiryHistoryState => ({ phase: "idle", page: 1, total: 0,
  items: [], selectedId: null, detail: null, detailPhase: "idle", message: "", detailMessage: "" });

export function createInquiryHistoryController(source: InquiryHistorySource | undefined, publish: (state: InquiryHistoryState) => void) {
  let state = initialInquiryHistory(), disposed = false;
  let listRequest: AbortController | null = null, detailRequest: AbortController | null = null;
  function set(next: InquiryHistoryState) { state = next; if (!disposed) publish(state); }
  function authFailure(error: unknown) {
    return error instanceof ApiError && [401, 403].includes(error.status) || error instanceof Error && error.name === "InquirySessionChanged";
  }
  function unavailable() {
    listRequest?.abort(); detailRequest?.abort();
    set({ ...initialInquiryHistory(), phase: "unavailable", message: "로그인 상태가 변경되었어요. MY PAGE로 돌아가 다시 확인해주세요." });
  }
  return {
    invalidate: unavailable,
    async load(page = 1) {
      if (disposed || state.phase === "unavailable") return;
      listRequest?.abort(); detailRequest?.abort();
      const request = new AbortController(); listRequest = request;
      set({ ...initialInquiryHistory(), page, phase: "loading" });
      if (!source) { set({ ...state, phase: "error", message: "로그인 후 문의 내역과 답변을 확인할 수 있어요." }); return; }
      try {
        const result = await source.list(page, request.signal);
        if (disposed || request.signal.aborted || listRequest !== request) return;
        set({ ...state, phase: "ready", items: result.items, page: result.page, total: result.total });
      } catch (error) {
        if (disposed || request.signal.aborted || listRequest !== request) return;
        if (authFailure(error)) { unavailable(); return; }
        set({ ...state, phase: "error", message: "문의 내역을 불러오지 못했어요. 다시 시도해주세요." });
      }
    },
    async select(id: string) {
      if (disposed || !source || state.phase !== "ready" || !state.items.some(row => row.inquiryId === id)) return;
      detailRequest?.abort(); const request = new AbortController(); detailRequest = request;
      set({ ...state, selectedId: id, detail: null, detailPhase: "loading", detailMessage: "" });
      try {
        const result = await source.detail(id, request.signal);
        if (disposed || request.signal.aborted || detailRequest !== request) return;
        set({ ...state, detail: result, detailPhase: "ready" });
      } catch (error) {
        if (disposed || request.signal.aborted || detailRequest !== request) return;
        if (authFailure(error)) { unavailable(); return; }
        set({ ...state, detailPhase: "error", detailMessage: error instanceof ApiError && error.status === 404
          ? "문의 내역을 찾을 수 없어요. 목록을 새로고침해주세요." : "문의와 답변을 불러오지 못했어요. 다시 시도해주세요." });
      }
    },
    cancel() { disposed = true; listRequest?.abort(); detailRequest?.abort(); state = initialInquiryHistory(); },
  };
}
