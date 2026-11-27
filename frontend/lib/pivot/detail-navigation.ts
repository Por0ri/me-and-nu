import type { HomeDetailTarget } from "./home";

export type HomeReturnSnapshot = {
  path: string;
  kind: "demo" | "service";
  scrollY: number;
  query: string;
  focusContentId: string;
  /** 돌아왔을 때 포커스를 둘 아래 메뉴 · 알림 버튼 (talk: TALK 에 다녀왔을 때, home-feed-memory.ts) */
  focusNavigation?: "my" | "saved" | "notification" | "talk";
  tracks: { postId: string; index: number }[];
};

// Local pathname and encoded identifiers only; no user-supplied redirect URL.
export function detailHref(path: string, target: HomeDetailTarget): string {
  const params = new URLSearchParams({ view: "detail", postId: target.postId, contentId: target.contentId });
  if (target.topicId !== undefined) {
    if (!Number.isSafeInteger(target.topicId) || target.topicId <= 0) throw new Error("Invalid Topic identity");
    params.set("topicId", String(target.topicId));
  }
  return `${path}?${params}`;
}
export function readHomeReturn(value: unknown, path: string, kind: "demo" | "service"): HomeReturnSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const snapshot = value as HomeReturnSnapshot;
  if (snapshot.path !== path || snapshot.kind !== kind || !Number.isFinite(snapshot.scrollY) || snapshot.scrollY < 0
    || typeof snapshot.query !== "string" || typeof snapshot.focusContentId !== "string" || !Array.isArray(snapshot.tracks)
    || (snapshot.focusNavigation !== undefined && !["my", "saved", "notification", "talk"].includes(snapshot.focusNavigation))
    || snapshot.tracks.some((track) => !track || typeof track.postId !== "string" || !Number.isFinite(track.index) || track.index < 0)) return null;
  return snapshot;
}
