import { getSubtopics, getTopics } from "@/lib/api";
import type { OnboardingTopicOption } from "@/types/consumer";
import provisionalInterests from "@/lib/onboarding/provisional-interests.json";

// Local demo choices pending the team's taxonomy review. Resolve real API IDs;
// existing work/album subscriptions stay valid but are not onboarding choices.
function onboardingInterests(
  topicCode: string,
  items: OnboardingTopicOption["subtopicOptions"],
) {
  const names = (provisionalInterests as Record<string, string[]>)[topicCode];
  if (!names) return items;
  return names.map((name) => {
    const matches = items.filter((item) => item.label === name);
    if (matches.length !== 1) {
      throw new Error(`Onboarding interest is missing or ambiguous: ${topicCode}/${name}`);
    }
    return matches[0];
  });
}

export function positiveApiId(value: string): number {
  if (!/^[1-9]\d*$/.test(value)) {
    throw new Error("API ID must be a positive integer.");
  }
  const id = Number(value);
  if (!Number.isSafeInteger(id)) {
    throw new Error("API ID exceeds the safe integer range.");
  }
  return id;
}

async function loadSubtopics(topicId: number) {
  const items: OnboardingTopicOption["subtopicOptions"] = [];
  const seenCursors = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await getSubtopics(topicId, { cursor, limit: 100 });
    items.push(...page.items.map((item) => ({ id: String(item.subtopicId), label: item.name })));
    if (!page.nextCursor) break;
    if (seenCursors.has(page.nextCursor) || seenCursors.size >= 1000) {
      throw new Error("Subtopic pagination did not complete.");
    }
    seenCursors.add(page.nextCursor);
    cursor = page.nextCursor;
  } while (cursor);
  return items;
}

export async function loadApiTopicOptions(): Promise<OnboardingTopicOption[]> {
  const response = await getTopics();
  return Promise.all(
    response.topics.map(async (topic) => ({
      id: String(topic.id),
      code: topic.code,
      label: topic.name,
      subtopicOptions: onboardingInterests(topic.code, await loadSubtopics(topic.id)),
    })),
  );
}
