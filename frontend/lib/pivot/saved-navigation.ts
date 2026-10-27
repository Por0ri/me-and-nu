export type SavedReturnSnapshot = {
  path: string; kind: "demo" | "service"; scrollY: number; focusContentId: string;
};
export function readSavedReturn(value: unknown, path: string, kind: "demo" | "service"): SavedReturnSnapshot | null {
  if (!value || typeof value !== "object") return null;
  const snapshot = value as SavedReturnSnapshot;
  if (snapshot.path !== path || snapshot.kind !== kind || !Number.isFinite(snapshot.scrollY) || snapshot.scrollY < 0
    || typeof snapshot.focusContentId !== "string") return null;
  return snapshot;
}
