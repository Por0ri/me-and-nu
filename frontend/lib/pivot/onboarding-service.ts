import { ApiError, apiRequest, getSession, getTopics, type ConsentInput, type Session, type TopicsResponse } from "@/lib/api";
import type { ConsentDraft } from "./consent";
import { isSelectionComplete, sameRanking, type Q1Choice, type UiSelection } from "./onboarding";
import { isActiveSession, isPendingConsumer, requirePivotApi } from "./services";

export type OnboardingProfile = { nickname: string };
export type OnboardingProfileValidation =
  | { valid: true; profile: OnboardingProfile }
  | { valid: false; errors: Partial<Record<keyof OnboardingProfile, string>> };

/** Same nickname rule as the backend (2026-11 design): 2~20 Korean (jamo too), English or digit characters after trimming. */
export const NICKNAME_MIN = 2;
export const NICKNAME_MAX = 20;
export const NICKNAME_RULE = "닉네임은 2~20자의 한글·영문·숫자만 쓸 수 있어요.";
const NICKNAME_CHARACTERS = /^[0-9A-Za-z가-힣ㄱ-ㅣ]+$/;

/** Why an already trimmed nickname breaks the rule: a space or symbol inside, or its length. */
export function nicknameProblem(nickname: string): "characters" | "length" | null {
  if (nickname && !NICKNAME_CHARACTERS.test(nickname)) return "characters";
  const length = [...nickname].length;
  return length < NICKNAME_MIN || length > NICKNAME_MAX ? "length" : null;
}

/** Which parts of the rule the typed text breaks, as the screen marks them: "2~20자" (length) and
 * "공백·특수문자 제외" (characters, a space at either end included so it never looks accepted). Empty breaks nothing yet. */
export function nicknameRuleBreaks(typed: string): { length: boolean; characters: boolean } {
  const length = [...typed].length;
  return {
    length: length > 0 && (length < NICKNAME_MIN || length > NICKNAME_MAX),
    characters: length > 0 && !NICKNAME_CHARACTERS.test(typed),
  };
}

export const NICKNAME_TAKEN_MESSAGE = "중복된 닉네임입니다";
export type NicknameCheck = { nickname: string; available: boolean; reason: "invalid" | "taken" | null };

/** Asks whether another member who finished onboarding already uses the nickname (completion checks again). */
export async function checkNickname(nickname: string, signal?: AbortSignal, guard: () => void = requirePivotApi): Promise<NicknameCheck> {
  guard();
  return apiRequest<NicknameCheck>(`/api/v1/onboarding/nickname?nickname=${encodeURIComponent(nickname)}`, { signal });
}

/** Birth-date collection and age policy remain deferred; only nickname is collected. */
export function validateOnboardingProfile(profile: OnboardingProfile): OnboardingProfileValidation {
  const nickname = typeof profile?.nickname === "string" ? profile.nickname.trim() : "";
  return nicknameProblem(nickname) ? { valid: false, errors: { nickname: NICKNAME_RULE } } : { valid: true, profile: { nickname } };
}

/** The version 2 answers onboarding completion and the My Page taste edit send. Q3 goes as the ranking
 * (consumptionTypes, rank 1 first) and rank 1 again as consumptionType, the one answer a server from before the
 * ranking reads; the backend requires the two to agree. Responses carry both, a server from before the ranking rank 1 only. */
export type AnswersV2 = { version: 2; favoriteEntityIds: number[]; preferenceTagIds: number[]; consumptionTypes?: Q1Choice[]; consumptionType: Q1Choice };
export function answersV2(selection: UiSelection): AnswersV2 {
  return { version: 2, favoriteEntityIds: [...selection.favoriteEntityIds], preferenceTagIds: [...selection.preferenceTagIds],
    consumptionTypes: [...selection.consumptionTypes], consumptionType: selection.consumptionTypes[0] };
}

/** A server from before the Q3 ranking forbids the unknown field: 422 VALIDATION_ERROR on answers.consumptionTypes. */
export function rejectsConsumptionRanking(error: unknown): boolean {
  return error instanceof ApiError && error.status === 422 && error.code === "VALIDATION_ERROR"
    && Array.isArray(error.fields) && error.fields.some(field => field?.field === "answers.consumptionTypes");
}

/** Sends the answers with the Q3 ranking. A server from before it refuses them before saving anything, so it gets
 * them once more with rank 1 alone. Returns the answers that were last sent, for the response check. */
export async function sendAnswersV2<I extends { answers: AnswersV2 }, R>(input: I, send: (input: I) => Promise<R>): Promise<{ response: R; sent: I }> {
  try {
    return { response: await send(input), sent: input };
  } catch (error) {
    if (!input.answers.consumptionTypes || !rejectsConsumptionRanking(error)) throw error;
    const answers: AnswersV2 = { ...input.answers };
    delete answers.consumptionTypes;
    const sent: I = { ...input, answers };
    return { response: await send(sent), sent };
  }
}

/** The Q3 answer in a response is the one sent: rank 1, and the whole ranking in order when the response has one
 * (a server from before the ranking answers with rank 1 alone). */
export function sameConsumptionAnswer(answer: { consumptionType?: unknown; consumptionTypes?: unknown } | null | undefined, sent: AnswersV2): boolean {
  const ranking = sent.consumptionTypes ?? [sent.consumptionType];
  const returned = answer?.consumptionTypes;
  return answer?.consumptionType === ranking[0] && (returned == null || (Array.isArray(returned) && sameRanking(returned, ranking)));
}

export type OnboardingCompleteInput = OnboardingProfile & {
  accountType: "user";
  consents: ConsentInput[];
  topicId: number;
  answers: AnswersV2;
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
    response.answers?.version !== 2 || !sameConsumptionAnswer(response.answers, input.answers) ||
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
    if (!selection || !isSelectionComplete(selection)) throw new Error("작품과 소비 유형을 선택하고, 장르와 소재를 각각 1~7개 선택해 주세요.");
    const consents = copyConsents(consent);
    const answers = answersV2(selection);
    const before = await api.getSession();
    if (isActiveSession(before)) return { session: before, recovered: true };
    if (!isPendingConsumer(before)) {
      throw new ApiError("로그인 상태를 다시 확인해 주세요.", before?.authenticated ? 409 : 401,
        before?.authenticated ? "STATE_CONFLICT" : "AUTH_REQUIRED");
    }
    const topicId = animeTopicId(await api.getTopics());
    if (topicId !== selection.topicId) throw new Error("관심 분야가 변경됐어요. 작품부터 다시 선택해 주세요.");
    const input: OnboardingCompleteInput = { ...validated.profile, accountType: "user", consents, topicId, answers };
    let completed: { response: OnboardingCompleteResponse; sent: OnboardingCompleteInput };
    try {
      completed = await sendAnswersV2(input, body => api.complete(body));
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
    const { response, sent } = completed;
    validateCompletion(response, sent);
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
