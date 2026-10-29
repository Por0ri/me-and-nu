import { apiRequest, getSession, getTopics } from "@/lib/api";
import { animeTopicId } from "./onboarding-service";
import { selectionKey, validId, type OnboardingWork, type PreferenceOption } from "./onboarding";
import { requirePivotApi } from "./services";

export type WorkPage = { items: OnboardingWork[]; nextCursor: string | null };
export type PreferenceOptions = { entityIds: number[]; genres: PreferenceOption[]; themes: PreferenceOption[]; minSelections: 1; maxSelections: null };
export type OnboardingCatalog = {
  resolveTopic(): Promise<number>;
  search(topicId: number, q: string, cursor?: string, signal?: AbortSignal): Promise<WorkPage>;
  options(topicId: number, entityIds: number[], signal?: AbortSignal): Promise<PreferenceOptions>;
};

function fail(): never { throw new Error("작품 정보를 확인하지 못했어요. 잠시 후 다시 시도해 주세요."); }
export function validateWorkPage(value: WorkPage): WorkPage {
  if (!Array.isArray(value?.items) || (value.nextCursor !== null && typeof value.nextCursor !== "string")) fail();
  const seen = new Set<number>();
  for (const work of value.items) {
    if (!validId(work?.entityId) || seen.has(work.entityId) || typeof work.name !== "string" || !work.name.trim() ||
      !Array.isArray(work.aliases) || !work.aliases.every(alias => typeof alias === "string") || typeof work.selectable !== "boolean" ||
      (work.imageUrl !== null && typeof work.imageUrl !== "string") ||
      (work.releaseYear !== null && !Number.isSafeInteger(work.releaseYear)) ||
      (work.mediaFormat !== null && typeof work.mediaFormat !== "string")) fail();
    seen.add(work.entityId);
  }
  return value;
}
export function validatePreferenceOptions(value: PreferenceOptions, ids: number[]): PreferenceOptions {
  if (!Array.isArray(value?.entityIds) || value.entityIds.length !== ids.length || !value.entityIds.every(validId) ||
    selectionKey(value.entityIds) !== selectionKey(ids) || !Array.isArray(value.genres) || !Array.isArray(value.themes) ||
    value.minSelections !== 1 || value.maxSelections !== null) fail();
  const seen = new Set<number>();
  for (const kind of ["genre", "theme"] as const) {
    for (const tag of kind === "genre" ? value.genres : value.themes) {
      if (!validId(tag?.tagId) || seen.has(tag.tagId) || tag.kind !== kind || typeof tag.code !== "string" ||
        typeof tag.label !== "string" || !tag.label.trim() || !Array.isArray(tag.sourceEntityIds) ||
        new Set(tag.sourceEntityIds).size !== tag.sourceEntityIds.length || !tag.sourceEntityIds.every(id => ids.includes(id))) fail();
      seen.add(tag.tagId);
    }
  }
  return value;
}

export const serviceCatalog: OnboardingCatalog = {
  async resolveTopic() { requirePivotApi(); return animeTopicId(await getTopics()); },
  async search(topicId, q, cursor, signal) {
    requirePivotApi();
    const query = new URLSearchParams({ topicId: String(topicId), q: q.trim(), limit: "20" });
    if (cursor) query.set("cursor", cursor);
    return validateWorkPage(await apiRequest<WorkPage>(`/api/v1/onboarding/works?${query}`, { signal }));
  },
  async options(topicId, entityIds, signal) {
    requirePivotApi();
    // A retry must pick up the current CSRF token after a session refresh.
    await getSession();
    return validatePreferenceOptions(await apiRequest<PreferenceOptions>("/api/v1/onboarding/preference-options", {
      method: "POST", body: JSON.stringify({ topicId, entityIds }), signal,
    }), entityIds);
  },
};

// Explicit design/demo fixtures only. Service calls never fall back to this list.
export const demoWorks: OnboardingWork[] = [
  { entityId: 901, name: "[예시] 별빛 우체국", aliases: ["별빛", "우체국"], imageUrl: null, releaseYear: null, mediaFormat: "예시", selectable: true, unavailableReason: null },
  { entityId: 902, name: "[예시] 구름 정원의 로봇", aliases: ["구름", "로봇"], imageUrl: null, releaseYear: null, mediaFormat: "예시", selectable: true, unavailableReason: null },
];
export const demoOptions: PreferenceOption[] = [
  { tagId: 911, kind: "genre", code: "demo_fantasy", label: "판타지", sourceEntityIds: [901] },
  { tagId: 912, kind: "genre", code: "demo_adventure", label: "모험", sourceEntityIds: [901, 902] },
  { tagId: 913, kind: "theme", code: "demo_friendship", label: "우정", sourceEntityIds: [901, 902] },
  { tagId: 914, kind: "theme", code: "demo_robot", label: "로봇", sourceEntityIds: [902] },
];
export const demoCatalog: OnboardingCatalog = {
  async resolveTopic() { return 900; },
  async search(_topicId, q) { return { items: demoWorks.filter(work => `${work.name} ${work.aliases.join(" ")}`.includes(q.trim())), nextCursor: null }; },
  async options(_topicId, entityIds) {
    const options = demoOptions.map(tag => ({ ...tag, sourceEntityIds: tag.sourceEntityIds.filter(id => entityIds.includes(id)) })).filter(tag => tag.sourceEntityIds.length);
    return { entityIds, genres: options.filter(tag => tag.kind === "genre"), themes: options.filter(tag => tag.kind === "theme"), minSelections: 1, maxSelections: null };
  },
};
