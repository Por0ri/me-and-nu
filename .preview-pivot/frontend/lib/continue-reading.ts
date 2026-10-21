import type { Content } from "@/types/content";

export type ContentReadingOrigin = "home" | "saved";

export function normalizeContentReadingOrigin(
  value: unknown,
): ContentReadingOrigin {
  return value === "saved" ? "saved" : "home";
}

export function selectContinueReadingContents(
  contents: Content[],
  currentContentId: string,
  limit = 4,
): Content[] {
  if (!Number.isFinite(limit) || limit < 1) return [];

  const seen = new Set<string>();
  const uniqueContents = contents.filter((content) => {
    if (seen.has(content.id)) return false;
    seen.add(content.id);
    return true;
  });
  const currentIndex = uniqueContents.findIndex(
    (content) => content.id === currentContentId,
  );
  const candidates =
    currentIndex === -1
      ? uniqueContents
      : [
          ...uniqueContents.slice(currentIndex + 1),
          ...uniqueContents.slice(0, currentIndex),
        ];

  return candidates.slice(0, Math.floor(limit));
}
