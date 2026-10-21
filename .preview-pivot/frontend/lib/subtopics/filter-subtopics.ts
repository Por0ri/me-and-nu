import type { SubtopicOption } from "@/types/consumer";

export function filterSubtopics(
  options: readonly SubtopicOption[],
  query: string,
): SubtopicOption[] {
  const normalizedQuery = query.trim().toLowerCase();

  return options.filter((option) =>
    option.label.toLowerCase().includes(normalizedQuery),
  );
}
