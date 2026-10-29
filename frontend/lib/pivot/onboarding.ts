// Existing consumption choices move to Q3. Their codes are not catalog IDs.
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
export type OnboardingWork = { entityId: number; name: string; aliases: string[]; imageUrl: string | null; releaseYear: number | null; mediaFormat: string | null; selectable: boolean; unavailableReason: string | null };
export const MAX_PREFERENCE_TAGS_PER_KIND = 7;
export type PreferenceOption = { tagId: number; kind: "genre" | "theme"; code: string; label: string; sourceEntityIds: number[] };
export type OnboardingStep = "interest" | "q1" | "q2" | "q3" | "result";
export type UiSelection = { interest: "animation"; topicId: number; favoriteEntityIds: number[]; preferenceTagIds: number[]; consumptionType: Q1Choice };
export type OnboardingState = { step: OnboardingStep; interest: "animation" | null; topicId: number | null; favoriteWorks: OnboardingWork[]; preferenceOptions: PreferenceOption[]; preferenceTagIds: number[]; optionsKey: string | null; consumptionType: Q1Choice | null };
export type OnboardingAction =
  | { type: "toggle-interest" }
  | { type: "set-topic"; topicId: number }
  | { type: "toggle-work"; work: OnboardingWork }
  | { type: "invalidate-options" }
  | { type: "set-options"; topicId: number; entityIds: number[]; options: PreferenceOption[] }
  | { type: "toggle-tag"; tagId: number }
  | { type: "choose-consumption"; value: string }
  | { type: "advance" | "back"; from: OnboardingStep }
  | { type: "reselect"; from: "result" }
  | { type: "reset" };
export const initialOnboardingState = (): OnboardingState => ({ step: "interest", interest: null, topicId: null, favoriteWorks: [], preferenceOptions: [], preferenceTagIds: [], optionsKey: null, consumptionType: null });
const isQ1 = (value: unknown): value is Q1Choice => q1Choices.some((choice) => choice.id === value);
export const validId = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0;
export const selectionKey = (ids: number[]) => [...ids].sort((a, b) => a - b).join(",");
export function workFormatLabel(format: string | null): string {
  return ({ SERIES: "시리즈", TV: "TV 애니메이션", TV_SHORT: "단편 시리즈", MOVIE: "극장판", OVA: "OVA", ONA: "웹 애니메이션", SPECIAL: "스페셜", MUSIC: "뮤직비디오", "예시": "예시" } as Record<string, string>)[format ?? ""] ?? "애니메이션";
}
const validIds = (ids: unknown, max = Infinity, min = 1): ids is number[] => Array.isArray(ids) && ids.length >= min && ids.length <= max && ids.every(validId) && new Set(ids).size === ids.length;
// The DTO contains IDs only. State validation and the backend check their current kinds.
export function isSelectionComplete(selection: UiSelection): boolean {
  return selection?.interest === "animation" && validId(selection.topicId) && validIds(selection.favoriteEntityIds, 5) && validIds(selection.preferenceTagIds, MAX_PREFERENCE_TAGS_PER_KIND * 2, 2) && isQ1(selection.consumptionType);
}
export function hasCurrentOptions(state: OnboardingState): boolean {
  return state.favoriteWorks.length > 0 && state.optionsKey === selectionKey(state.favoriteWorks.map(work => work.entityId));
}
export function selectedPreferenceCount(state: OnboardingState, kind: PreferenceOption["kind"]): number {
  return state.preferenceOptions.filter(tag => tag.kind === kind && state.preferenceTagIds.includes(tag.tagId)).length;
}
function hasRequiredPreferences(state: OnboardingState): boolean {
  return hasCurrentOptions(state) && validIds(state.preferenceTagIds, MAX_PREFERENCE_TAGS_PER_KIND * 2, 2)
    && state.preferenceTagIds.every(id => state.preferenceOptions.some(tag => tag.tagId === id))
    && (["genre", "theme"] as const).every(kind => {
      const count = selectedPreferenceCount(state, kind);
      return count >= 1 && count <= MAX_PREFERENCE_TAGS_PER_KIND;
    });
}
export function isStateComplete(state: OnboardingState): boolean {
  return state.interest === "animation" && validId(state.topicId) && validIds(state.favoriteWorks.map(work => work.entityId), 5) && hasRequiredPreferences(state) && isQ1(state.consumptionType);
}
export function canAdvance(state: OnboardingState): boolean {
  if (state.step === "interest") return state.interest === "animation";
  if (state.step === "q1") return state.interest === "animation" && validId(state.topicId) && validIds(state.favoriteWorks.map(work => work.entityId), 5);
  if (state.step === "q2") return hasRequiredPreferences(state);
  if (state.step === "q3") return isStateComplete(state);
  return false;
}
export function onboardingReducer(state: OnboardingState, action: OnboardingAction): OnboardingState {
  if (action.type === "reset") return initialOnboardingState();
  // Ordinary back preserves answers. A changed work set refreshes the catalog and its source hints.
  if (action.type === "reselect") return state.step === action.from
    ? { ...initialOnboardingState(), step: "q1", interest: state.interest, topicId: state.topicId } : state;
  if (action.type === "toggle-interest") return state.step === "interest"
    ? { ...state, interest: state.interest ? null : "animation" } : state;
  if (action.type === "set-topic") return !validId(action.topicId) || action.topicId === state.topicId ? state
    : { ...initialOnboardingState(), step: state.step === "interest" ? "interest" : "q1", interest: state.interest, topicId: action.topicId };
  if (action.type === "toggle-work") {
    if (state.step !== "q1" || !validId(action.work.entityId) || !action.work.selectable) return state;
    const exists = state.favoriteWorks.some(work => work.entityId === action.work.entityId);
    if (!exists && state.favoriteWorks.length >= 5) return state;
    return { ...state, favoriteWorks: exists ? state.favoriteWorks.filter(work => work.entityId !== action.work.entityId) : [...state.favoriteWorks, action.work], preferenceOptions: [], preferenceTagIds: [], optionsKey: null };
  }
  if (action.type === "invalidate-options") return { ...state, optionsKey: null };
  if (action.type === "set-options") {
    if (state.step !== "q2" || action.topicId !== state.topicId || selectionKey(action.entityIds) !== selectionKey(state.favoriteWorks.map(work => work.entityId))) return state;
    return { ...state, preferenceOptions: action.options, optionsKey: selectionKey(action.entityIds), preferenceTagIds: state.preferenceTagIds.filter(id => action.options.some(tag => tag.tagId === id)) };
  }
  if (action.type === "toggle-tag") {
    const tag = state.preferenceOptions.find(option => option.tagId === action.tagId);
    if (state.step !== "q2" || !hasCurrentOptions(state) || !tag) return state;
    const selected = state.preferenceTagIds.includes(action.tagId);
    if (!selected && selectedPreferenceCount(state, tag.kind) >= MAX_PREFERENCE_TAGS_PER_KIND) return state;
    return { ...state, preferenceTagIds: selected ? state.preferenceTagIds.filter(id => id !== action.tagId) : [...state.preferenceTagIds, action.tagId] };
  }
  if (action.type === "choose-consumption") return state.step === "q3" && isQ1(action.value) ? { ...state, consumptionType: action.value } : state;
  // Stale or double-dispatched navigation can never skip a step.
  if (action.from !== state.step) return state;
  if (action.type === "advance") {
    if (!canAdvance(state)) return state;
    return { ...state, step: state.step === "interest" ? "q1" : state.step === "q1" ? "q2" : state.step === "q2" ? "q3" : "result" };
  }
  if (action.type === "back") return { ...state, step: state.step === "result" ? "q3" : state.step === "q3" ? "q2" : state.step === "q2" ? "q1" : "interest" };
  return state;
}
export function buildSelectionDraft(state: OnboardingState): UiSelection {
  if (!isStateComplete(state)) throw new Error("작품과 소비 유형을 선택하고, 장르와 소재를 각각 1~7개 선택해 주세요.");
  return { interest: "animation", topicId: state.topicId!, favoriteEntityIds: state.favoriteWorks.map(work => work.entityId), preferenceTagIds: [...state.preferenceTagIds], consumptionType: state.consumptionType! };
}
export function selectionSummary(state: OnboardingState) {
  return {
    works: state.favoriteWorks.map(work => work.name).join(" · "),
    tags: state.preferenceOptions.filter(tag => state.preferenceTagIds.includes(tag.tagId)).map(tag => tag.label).join(" · "),
    consumption: q1Choices.find((choice) => choice.id === state.consumptionType)?.summary ?? "",
  };
}
