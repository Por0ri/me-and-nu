import { ApiError, apiRequest, getSession, getTopics, type ConsentInput, type Session, type TopicsResponse } from "@/lib/api";
import { getAgeEligibility, validateBirthdate } from "@/lib/onboarding/birthdate";
import type { ConsentDraft } from "./consent";
import { isSelectionComplete, type UiSelection } from "./onboarding";
import { isActiveSession, isPendingConsumer, requirePivotApi } from "./services";

export type OnboardingProfile = { nickname: string; birthDate: string };
export type OnboardingProfileValidation =
  | { valid: true; profile: OnboardingProfile }
  | { valid: false; errors: Partial<Record<keyof OnboardingProfile, string>> };

/** Match Python's Unicode character count and the backend's UTC calendar age check. */
export function validateOnboardingProfile(profile: OnboardingProfile, referenceDate = new Date()): OnboardingProfileValidation {
  const errors: Partial<Record<keyof OnboardingProfile, string>> = {};
  const nickname = typeof profile?.nickname === "string" ? profile.nickname.trim() : "";
  if (!nickname || [...nickname].length > 30) errors.nickname = "닉네임을 1~30자로 입력해 주세요.";
  const birthDate = typeof profile?.birthDate === "string" ? profile.birthDate : "";
  // The existing calendar helper uses local date components; supply the backend's UTC day.
  const calendarDay = new Date(referenceDate.getUTCFullYear(), referenceDate.getUTCMonth(), referenceDate.getUTCDate(), 12);
  const birth = validateBirthdate(birthDate, calendarDay);
  if (!birth.valid) {
    errors.birthDate = birth.reason === "required" ? "생년월일을 입력해 주세요."
      : birth.reason === "future" ? "미래 날짜는 생년월일로 입력할 수 없어요." : "올바른 생년월일을 입력해 주세요.";
  } else if (getAgeEligibility(birthDate, calendarDay) !== "eligible") {
    errors.birthDate = "만 14세 이상부터 가입할 수 있어요.";
  }
  return Object.keys(errors).length ? { valid: false, errors } : { valid: true, profile: { nickname, birthDate } };
}

export type OnboardingCompleteInput = OnboardingProfile & {
  accountType: "user";
  consents: ConsentInput[];
  topicId: number;
  answers: { q1: UiSelection["q1"]; q2: UiSelection["q2"] };
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

function animeTopicId(response: TopicsResponse): number {
  const matches = Array.isArray(response?.topics) ? response.topics.filter((topic) => topic?.code === "anime") : [];
  if (matches.length !== 1 || !Number.isSafeInteger(matches[0].id) || matches[0].id <= 0) {
    throw new Error("애니메이션 관심 분야를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.");
  }
  return matches[0].id;
}

function validateCompletion(response: OnboardingCompleteResponse, input: OnboardingCompleteInput): void {
  if (response?.onboardingCompleted !== true || response.user?.accountType !== "user" ||
    !Number.isSafeInteger(response.user?.id) || response.user.id <= 0 || response.activeTopic?.id !== input.topicId ||
    response.answers?.q1 !== input.answers.q1 || response.answers?.q2 !== input.answers.q2) {
    throw new Error("가입 완료 응답을 확인하지 못했어요. 다시 확인해 주세요.");
  }
}

/** A retry first checks the session; a completed account is never submitted again. */
export function createOnboardingService(
  api: OnboardingApi = defaultApi,
  guard: () => void = requirePivotApi,
  now: () => Date = () => new Date(),
): OnboardingSubmissionService {
  let pending: Promise<OnboardingCompletion> | null = null;

  async function submit(selection: UiSelection, consent: ConsentDraft, profile: OnboardingProfile): Promise<OnboardingCompletion> {
    guard();
    const validated = validateOnboardingProfile(profile, now());
    if (!validated.valid) throw new Error(validated.errors.nickname ?? validated.errors.birthDate);
    if (!selection || !isSelectionComplete(selection)) throw new Error("관심 분야와 두 답변을 모두 선택해 주세요.");
    const consents = copyConsents(consent);
    const answers = { q1: selection.q1, q2: selection.q2 };
    const before = await api.getSession();
    if (isActiveSession(before)) return { session: before, recovered: true };
    if (!isPendingConsumer(before)) {
      throw new ApiError("로그인 상태를 다시 확인해 주세요.", before?.authenticated ? 409 : 401,
        before?.authenticated ? "STATE_CONFLICT" : "AUTH_REQUIRED");
    }
    const topicId = animeTopicId(await api.getTopics());
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
