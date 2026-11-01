// My Page taste edit (temporary, 2026-11 user test): onboarding Q1~Q3 again with the saved answers.
// Remove together with /pivot/taste, the My Page button and the backend /me/taste router.
import { apiRequest, getSession, getTopics } from "@/lib/api";
import { initialOnboardingState, onboardingReducer, q1Choices, validId, type OnboardingAction, type OnboardingState, type OnboardingWork, type UiSelection } from "./onboarding";
import { validatePreferenceOptions, validateWorkPage, type OnboardingCatalog, type WorkPage } from "./onboarding-catalog";
import { animeTopicId } from "./onboarding-service";
import { requirePivotApi } from "./services";

export type MyTaste = { topicId: number; works: OnboardingWork[]; preferenceTagIds: number[]; consumptionType: UiSelection["consumptionType"] | null };
type PreferenceOptionsPayload = Parameters<typeof validatePreferenceOptions>[0];

export function validateMyTaste(value: MyTaste, topicId: number): MyTaste {
  const ids = value?.preferenceTagIds;
  if (value?.topicId !== topicId || !Array.isArray(ids) || !ids.every(validId) || new Set(ids).size !== ids.length ||
    (value.consumptionType !== null && !q1Choices.some(choice => choice.id === value.consumptionType))) {
    throw new Error("내 취향을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.");
  }
  validateWorkPage({ items: value.works, nextCursor: null });
  return value;
}

// The onboarding search/chip APIs only accept accounts still onboarding; these are the same lists for members.
export const tasteCatalog: OnboardingCatalog = {
  async resolveTopic() { requirePivotApi(); return animeTopicId(await getTopics()); },
  async search(topicId, q, cursor, signal) {
    requirePivotApi();
    const query = new URLSearchParams({ topicId: String(topicId), q: q.trim(), limit: "20" });
    if (cursor) query.set("cursor", cursor);
    return validateWorkPage(await apiRequest<WorkPage>(`/api/v1/me/taste/works?${query}`, { signal }));
  },
  async options(topicId, entityIds, signal) {
    requirePivotApi();
    await getSession();
    return validatePreferenceOptions(await apiRequest<PreferenceOptionsPayload>("/api/v1/me/taste/preference-options", {
      method: "POST", body: JSON.stringify({ topicId, entityIds }), signal,
    }), entityIds);
  },
};

export async function loadMyTaste(): Promise<MyTaste> {
  requirePivotApi();
  const topicId = animeTopicId(await getTopics());
  return validateMyTaste(await apiRequest<MyTaste>(`/api/v1/me/taste?topicId=${topicId}`), topicId);
}

export async function saveMyTaste(selection: UiSelection): Promise<MyTaste> {
  requirePivotApi();
  await getSession();
  const answers = { version: 2, favoriteEntityIds: selection.favoriteEntityIds, preferenceTagIds: selection.preferenceTagIds, consumptionType: selection.consumptionType };
  return validateMyTaste(await apiRequest<MyTaste>("/api/v1/me/taste", {
    method: "PUT", body: JSON.stringify({ topicId: selection.topicId, answers }),
  }), selection.topicId);
}

/** Q1 opens with the saved answers; Q2 reloads the chip list and keeps the saved chips. */
export function tasteEditState(taste: MyTaste): OnboardingState {
  return {
    ...initialOnboardingState(), step: "q1", interest: "animation", topicId: taste.topicId,
    favoriteWorks: taste.works.filter(work => work.selectable).slice(0, 5),
    preferenceTagIds: [...taste.preferenceTagIds], consumptionType: taste.consumptionType,
  };
}

export type TasteEditAction = OnboardingAction | { type: "load"; state: OnboardingState } | { type: "edit-again" };
/** The onboarding reducer, except a changed work set keeps the chosen chips: every active chip stays selectable. */
export function tasteEditReducer(state: OnboardingState, action: TasteEditAction): OnboardingState {
  if (action.type === "load") return action.state;
  if (action.type === "edit-again") return state.step === "result" ? { ...state, step: "q1" } : state;
  const next = onboardingReducer(state, action);
  return action.type === "toggle-work" && next !== state ? { ...next, preferenceTagIds: state.preferenceTagIds } : next;
}
