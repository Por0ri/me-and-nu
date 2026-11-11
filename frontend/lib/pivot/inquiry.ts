import { ApiError, type InquiryInput, type InquiryResponse } from "@/lib/api";

export const INQUIRY_LIMIT = 500;
export const inquiryTypes = [{ value: "service", label: "서비스 문의" }, { value: "bug", label: "버그/오류 신고" }] as const;
export type InquiryDraft = { inquiryType: InquiryInput["inquiryType"] | null; detail: string };
export type InquiryState = InquiryDraft & {
  status: "editing" | "submitting" | "received" | "error" | "unavailable";
  message: string;
};
export type InquirySource = {
  kind: "service";
  assertCurrent(): void;
  submit(input: InquiryInput, signal: AbortSignal): Promise<InquiryResponse>;
};
export class InquirySessionChanged extends Error {}
// Python's schema counts Unicode code points, not UTF-16 code units.
export function inquiryLength(detail: string): number { return [...detail.trim()].length; }
export function inquiryValidation(draft: InquiryDraft): string {
  if (!inquiryTypes.some(type => type.value === draft.inquiryType)) return "문의 유형을 선택해주세요.";
  const length = inquiryLength(draft.detail);
  if (!length) return "세부 내용을 입력해주세요.";
  if (length > INQUIRY_LIMIT) return "세부 내용은 500자까지 입력할 수 있어요.";
  return "";
}
export function initialInquiryState(): InquiryState { return { inquiryType: null, detail: "", status: "editing", message: "" }; }

export function createInquiryController(source: InquirySource | undefined, demo: boolean, publish: (state: InquiryState) => void) {
  let state = initialInquiryState();
  let disposed = false;
  let active: AbortController | null = null;
  // Editing or rejecting a later attempt cannot establish whether an earlier one was received.
  let uncertainMessage = "";
  function withUncertainty(message: string) { return [message, uncertainMessage].filter(Boolean).join(" "); }
  function set(next: InquiryState) { state = next; if (!disposed) publish(state); }
  function unavailable(message: string) { uncertainMessage = ""; set({ ...initialInquiryState(), status: "unavailable", message }); }
  return {
    change(draft: Partial<InquiryDraft>) {
      if (disposed || ["submitting", "received", "unavailable"].includes(state.status)) return;
      set({ ...state, ...draft, status: "editing", message: uncertainMessage });
    },
    async submit() {
      if (disposed || active || ["received", "unavailable"].includes(state.status)) return;
      if (demo || source?.kind !== "service") {
        set({ ...state, status: "error", message: demo ? "Demo에서는 실제 문의를 접수하지 않아요. 로그인 후 이용해주세요." : "문의 서버에 연결되지 않았어요." });
        return;
      }
      const invalid = inquiryValidation(state);
      if (invalid) { set({ ...state, status: "error", message: withUncertainty(invalid) }); return; }
      const request = new AbortController();
      active = request; // Acquire synchronously, before session/CSRF reads.
      set({ ...state, status: "submitting", message: uncertainMessage });
      try {
        source.assertCurrent();
        const result = await source.submit({ inquiryType: state.inquiryType!, detail: state.detail.trim() }, request.signal);
        request.signal.throwIfAborted(); source.assertCurrent();
        if (!result || result.status !== "received" || !Number.isSafeInteger(result.inquiryId) || result.inquiryId <= 0) {
          throw new ApiError("Invalid inquiry response", 201, "INVALID_RESPONSE");
        }
        // The design's submitted state retains the text and clears the selection.
        uncertainMessage = "";
        set({ ...state, inquiryType: null, status: "received", message: "문의가 접수되었어요." });
      } catch (error) {
        if (disposed || request.signal.aborted) return;
        if (error instanceof InquirySessionChanged) {
          unavailable("로그인 상태가 변경되었어요. MY PAGE로 돌아가 다시 확인해주세요."); return;
        }
        if (error instanceof ApiError && error.status === 401) {
          unavailable("로그인이 만료되었어요. 다시 로그인해주세요."); return;
        }
        try { source.assertCurrent(); } catch {
          unavailable("로그인 상태가 변경되었어요. MY PAGE로 돌아가 다시 확인해주세요."); return;
        }
        const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.code === "INVALID_RESPONSE";
        if (uncertain) uncertainMessage = "문의 접수 결과를 확인할 수 없어요. 이미 접수되었을 수 있으니 같은 내용을 다시 보내면 중복 접수될 수 있습니다.";
        const message = uncertain ? "" : error instanceof ApiError && error.code === "CSRF_INVALID"
          ? "보안 토큰이 만료되었어요. 다시 시도해주세요."
          : error instanceof ApiError && error.status === 403
            ? "문의를 접수할 권한이 없어요. 로그인 및 가입 완료 상태를 확인한 뒤 다시 시도해주세요."
            : uncertainMessage ? "입력 내용을 확인하고 다시 시도해주세요."
              : "문의를 접수하지 못했어요. 입력 내용을 확인하고 다시 시도해주세요.";
        set({ ...state, status: "error", message: withUncertainty(message) });
      } finally { if (active === request) active = null; }
    },
    cancel() { disposed = true; active?.abort(); active = null; uncertainMessage = ""; state = initialInquiryState(); },
  };
}
