import type { HomeDetailTarget } from "./home";

export type HomeReturnSnapshot = {
  path: string;
  kind: "demo" | "service";
  scrollY: number;
  query: string;
  focusContentId: string;
  focusNavigation?: "my" | "saved";
  tracks: { postId: string; index: number }[];
};

// Local pathname and encoded identifiers only; no user-supplied redirect URL.
export function detailHref(path: string, target: HomeDetailTarget): string {
  return `${path}?${new URLSearchParams({ view: "detail", postId: target.postId, contentId: target.contentId })}`;
}
export function readHomeReturn(value: unknown, path: string, kind: "demo" | "service"): HomeReturnSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const snapshot = value as HomeReturnSnapshot;
  if (snapshot.path !== path || snapshot.kind !== kind || !Number.isFinite(snapshot.scrollY) || snapshot.scrollY < 0
    || typeof snapshot.query !== "string" || typeof snapshot.focusContentId !== "string" || !Array.isArray(snapshot.tracks)
    || (snapshot.focusNavigation !== undefined && snapshot.focusNavigation !== "my" && snapshot.focusNavigation !== "saved")
    || snapshot.tracks.some((track) => !track || typeof track.postId !== "string" || !Number.isFinite(track.index) || track.index < 0)) return null;
  return snapshot;
}
