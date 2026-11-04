// My Page 프로필 수정 (2026-11 design): change the nickname with the onboarding rule.
import { apiRequest, getProfile, updateProfile } from "@/lib/api";
import { maskProfileEmail } from "./my-page";
import type { NicknameCheck } from "./onboarding-service";
import { requirePivotApi } from "./services";

export type EditableProfile = { nickname: string; maskedEmail: string };

export async function loadEditableProfile(signal?: AbortSignal): Promise<EditableProfile> {
  requirePivotApi();
  const profile = await getProfile({ signal });
  if (typeof profile?.nickname !== "string" || !profile.nickname.trim()) throw new Error("프로필을 확인하지 못했어요.");
  return { nickname: profile.nickname, maskedEmail: maskProfileEmail(profile.email) };
}

/** GET /users/me/nickname: the onboarding duplicate check for a member; their own nickname counts as available. */
export async function checkMyNickname(nickname: string, signal?: AbortSignal, guard: () => void = requirePivotApi): Promise<NicknameCheck> {
  guard();
  return apiRequest<NicknameCheck>(`/api/v1/users/me/nickname?nickname=${encodeURIComponent(nickname)}`, { signal });
}

/** PATCH /users/me checks the rule and duplicates again (422 / 409 NICKNAME_TAKEN); returns the saved nickname. */
export async function saveMyNickname(nickname: string): Promise<string> {
  requirePivotApi();
  return (await updateProfile({ nickname })).nickname;
}
