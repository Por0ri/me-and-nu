import { ApiError, apiRequest, getSession, getTopics, type ConsentInput, type Session, type TopicsResponse } from "@/lib/api";
import type { ConsentDraft } from "./consent";
import { isSelectionComplete, type UiSelection } from "./onboarding";
import { isActiveSession, isPendingConsumer, requirePivotApi } from "./services";

export type OnboardingProfile = { nickname: string };
export type OnboardingProfileValidation =
  | { valid: true; profile: OnboardingProfile }
  | { valid: false; errors: Partial<Record<keyof OnboardingProfile, string>> };

/** Birth-date collection and age policy remain deferred; only nickname is collected. */
export function validateOnboardingProfile(profile: OnboardingProfile): OnboardingProfileValidation {
  const errors: Partial<Record<keyof OnboardingProfile, string>> = {};
  const nickname = typeof profile?.nickname === "string" ? profile.nickname.trim() : "";
  if (!nickname || [...nickname].length > 30) errors.nickname = "닉네임을 1~30자로 입력해 주세요.";
  return Object.keys(errors).length ? { valid: false, errors } : { valid: true, profile: { nickname } };
}

export type OnboardingCompleteInput = OnboardingProfile & {
  accountType: "user";
  consents: ConsentInput[];
  topicId: number;
  answers: { version: 2; favoriteEntityIds: number[]; preferenceTagIds: number[]; consumptionType: UiSelection["consumptionType"] };
};
export type OnboardingCompleteResponse = {
  user: { id: number; accountType: "user" };
  onboardingCompleted: boolean;
  activeTopic: { id: number; code: string; name: string; subtopicIds: number[] } | null;
  answers: OnboardingCompleteInput["answers"];
};
export type OnboardingCompletion = { session: Session; recovered: boolean };
export type OnboardingApi = {
  getSession(): Promise<Session>;
  getTopics(): Promise<TopicsResponse>;
  complete(input: OnboardingCompleteInput): Promise<OnboardingCompleteResponse>;
};
export interface OnboardingSubmissionService {
  readonly availability: "configured";
  submitSelection(selection: UiSelection, consent: ConsentDraft, profile: OnboardingProfile): Promise<OnboardingCompletion>;
}

const defaultApi: OnboardingApi = {
  getSession,
  getTopics,
  complete: (input) => apiRequest<OnboardingCompleteResponse>("/api/v1/onboarding/complete", {
    method: "POST", body: JSON.stringify(input),
  }),
};

function copyConsents(consent: ConsentDraft): ConsentInput[] {
  if (!Array.isArray(consent?.consents) || consent.consents.length === 0) {
    throw new ApiError("약관을 다시 확인하고 동의해 주세요.", 422, "REQUIRED_CONSENT_MISSING");
  }
  const seen = new Set<string>();
  return consent.consents.map((item) => {
    if (!item || typeof item.type !== "string" || !item.type.trim() || [...item.type].length > 50 ||
      typeof item.policyVersion !== "string" || !item.policyVersion.trim() || [...item.policyVersion].length > 50 ||
      typeof item.agreed !== "boolean" || seen.has(item.type)) {
      throw new ApiError("동의 항목을 확인할 수 없어요. 약관을 다시 확인해 주세요.", 422, "VALIDATION_ERROR");
    }
    seen.add(item.type);
    return { type: item.type, policyVersion: item.policyVersion, agreed: item.agreed };
  });
}

export function animeTopicId(response: TopicsResponse): number {
  const matches = Array.isArray(response?.topics) ? response.topics.filter((topic) => topic?.code === "anime") : [];
  if (matches.length !== 1 || !Number.isSafeInteger(matches[0].id) || matches[0].id <= 0) {
    throw new Error("애니메이션 관심 분야를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.");
  }
  return matches[0].id;
}

function validateCompletion(response: OnboardingCompleteResponse, input: OnboardingCompleteInput): void {
  const sameIds = (actual: unknown, expected: number[]) => Array.isArray(actual) && actual.length === expected.length && new Set(actual).size === actual.length && actual.every(id => expected.includes(id));
  if (response?.onboardingCompleted !== true || response.user?.accountType !== "user" ||
    !Number.isSafeInteger(response.user?.id) || response.user.id <= 0 || response.activeTopic?.id !== input.topicId ||
    response.answers?.version !== 2 || response.answers?.consumptionType !== input.answers.consumptionType ||
    !sameIds(response.answers?.favoriteEntityIds, input.answers.favoriteEntityIds) || !sameIds(response.answers?.preferenceTagIds, input.answers.preferenceTagIds)) {
    throw new Error("가입 완료 응답을 확인하지 못했어요. 다시 확인해 주세요.");
  }
}

/** A retry first checks the session; a completed account is never submitted again. */
export function createOnboardingService(
  api: OnboardingApi = defaultApi,
  guard: () => void = requirePivotApi,
): OnboardingSubmissionService {
  let pending: Promise<OnboardingCompletion> | null = null;

  async function submit(selection: UiSelection, consent: ConsentDraft, profile: OnboardingProfile): Promise<OnboardingCompletion> {
    guard();
    const validated = validateOnboardingProfile(profile);
    if (!validated.valid) throw new Error(validated.errors.nickname);
    if (!selection || !isSelectionComplete(selection)) throw new Error("작품, 장르·소재, 소비 유형을 모두 선택해 주세요.");
    const consents = copyConsents(consent);
    const answers: OnboardingCompleteInput["answers"] = { version: 2, favoriteEntityIds: [...selection.favoriteEntityIds], preferenceTagIds: [...selection.preferenceTagIds], consumptionType: selection.consumptionType };
    const before = await api.getSession();
    if (isActiveSession(before)) return { session: before, recovered: true };
    if (!isPendingConsumer(before)) {
      throw new ApiError("로그인 상태를 다시 확인해 주세요.", before?.authenticated ? 409 : 401,
        before?.authenticated ? "STATE_CONFLICT" : "AUTH_REQUIRED");
    }
    const topicId = animeTopicId(await api.getTopics());
    if (topicId !== selection.topicId) throw new Error("관심 분야가 변경됐어요. 작품부터 다시 선택해 주세요.");
    const input: OnboardingCompleteInput = { ...validated.profile, accountType: "user", consents, topicId, answers };
    let response: OnboardingCompleteResponse;
    try {
      response = await api.complete(input);
    } catch (error) {
      // A lost response may still have committed the account and rotated its cookie.
      // Policy/validation failures must reach the UI unchanged so it can revisit consent.
      if (!(error instanceof ApiError) || error.status >= 500 || error.code === "ONBOARDING_ALREADY_COMPLETED") {
        try {
          const recovered = await api.getSession();
          if (isActiveSession(recovered)) return { session: recovered, recovered: true };
        } catch { /* Preserve the original failure when completion cannot be established. */ }
      }
      throw error;
    }
    validateCompletion(response, input);
    const after = await api.getSession();
    if (!isActiveSession(after) || after.user?.id !== response.user.id) {
      throw new Error("가입 후 로그인 상태를 확인하지 못했어요. 다시 확인해 주세요.");
    }
    return { session: after, recovered: false };
  }

  return {
    availability: "configured",
    submitSelection(selection, consent, profile) {
      if (!pending) {
        pending = Promise.resolve().then(() => submit(selection, consent, profile)).finally(() => { pending = null; });
      }
      return pending;
    },
  };
}

export const serviceOnboardingService = createOnboardingService();
export const pendingOnboardingService = serviceOnboardingService;
