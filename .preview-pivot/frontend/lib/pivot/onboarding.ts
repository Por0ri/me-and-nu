// UI-only choices from Figma. These IDs are not backend topic/subtopic IDs.
export const q1Choices = [
  { id: "world", lines: ["설정과 세계관을", "파고든다"], summary: "설정과 세계관", asset: "imgFrame303" },
  { id: "relationships", lines: ["캐릭터의 관계성과", "감정선에 빠진다"], summary: "캐릭터 관계성", asset: "imgFrame304" },
  { id: "ost", lines: ["관련 OST를", "찾아듣는다"], summary: "관련 OST", asset: "imgFrame305" },
  { id: "goods", lines: ["굿즈를 모으거나", "구매 정보를 찾는다"], summary: "굿즈 구매 정보", asset: "imgFrame306" },
  { id: "events", lines: ["팝업•전시•이벤트", "등을 찾아간다"], summary: "팝업 · 전시 · 이벤트", asset: "imgFrame307" },
  { id: "news", lines: ["새로운 소식을", "빠르게 확인한다"], summary: "새로운 소식", asset: "imgFrame308" },
] as const;
export const q2Choices = [
  { id: "releases", label: "신작/시즌 공개 소식", summary: "신작/시즌 공개 소식", asset: "imgFrame304" },
  { id: "characters", label: "최애 캐릭터 관련 소식", summary: "최애 캐릭터 관련 소식", asset: "imgFrame305" },
  { id: "restock", label: "굿즈 출시 및 재입고 정보", summary: "굿즈 출시 및 재입고 정보", asset: "imgFrame306" },
  { id: "events", label: "팝업 · 전시 · 콜라보 소식", summary: "팝업 · 전시 · 콜라보 소식", asset: "imgFrame307" },
] as const;
export type Q1Choice = typeof q1Choices[number]["id"];
export type Q2Choice = typeof q2Choices[number]["id"];
export type OnboardingStep = "interest" | "q1" | "q2" | "result";
export type UiSelection = { interest: "animation"; q1: Q1Choice; q2: Q2Choice };
export type OnboardingState = { step: OnboardingStep; interest: "animation" | null; q1: Q1Choice | null; q2: Q2Choice | null };
export type OnboardingAction =
  | { type: "toggle-interest" }
  | { type: "choose-q1"; value: string }
  | { type: "choose-q2"; value: string }
  | { type: "advance" | "back"; from: OnboardingStep }
  | { type: "reselect"; from: "result" }
  | { type: "reset" };
export const initialOnboardingState = (): OnboardingState => ({ step: "interest", interest: null, q1: null, q2: null });
const isQ1 = (value: unknown): value is Q1Choice => q1Choices.some((choice) => choice.id === value);
const isQ2 = (value: unknown): value is Q2Choice => q2Choices.some((choice) => choice.id === value);
export function isSelectionComplete(state: Pick<OnboardingState, "interest" | "q1" | "q2">): boolean {
  return state.interest === "animation" && isQ1(state.q1) && isQ2(state.q2);
}
export function canAdvance(state: OnboardingState): boolean {
  if (state.step === "interest") return state.interest === "animation";
  if (state.step === "q1") return state.interest === "animation" && isQ1(state.q1);
  if (state.step === "q2") return isSelectionComplete(state);
  return false;
}
export function onboardingReducer(state: OnboardingState, action: OnboardingAction): OnboardingState {
  if (action.type === "reset") return initialOnboardingState();
  // Only explicit result reselection clears answers; ordinary back preserves them.
  if (action.type === "reselect") return state.step === action.from
    ? { ...state, step: "q1", q1: null, q2: null } : state;
  if (action.type === "toggle-interest") return state.step === "interest"
    ? { ...state, interest: state.interest ? null : "animation" } : state;
  if (action.type === "choose-q1") return state.step === "q1" && isQ1(action.value) ? { ...state, q1: action.value } : state;
  if (action.type === "choose-q2") return state.step === "q2" && isQ2(action.value) ? { ...state, q2: action.value } : state;
  // Stale or double-dispatched navigation can never skip a step.
  if (action.from !== state.step) return state;
  if (action.type === "advance") {
    if (!canAdvance(state)) return state;
    return { ...state, step: state.step === "interest" ? "q1" : state.step === "q1" ? "q2" : "result" };
  }
  if (action.type === "back") return { ...state, step: state.step === "result" ? "q2" : state.step === "q2" ? "q1" : "interest" };
  return state;
}
export function buildSelectionDraft(state: OnboardingState): UiSelection {
  if (state.interest !== "animation" || !isQ1(state.q1) || !isQ2(state.q2)) throw new Error("관심 분야와 두 답변을 모두 선택해 주세요.");
  return { interest: state.interest, q1: state.q1, q2: state.q2 };
}
export function selectionSummary(state: OnboardingState) {
  return {
    q1: q1Choices.find((choice) => choice.id === state.q1)?.summary ?? "",
    q2: q2Choices.find((choice) => choice.id === state.q2)?.summary ?? "",
  };
}
