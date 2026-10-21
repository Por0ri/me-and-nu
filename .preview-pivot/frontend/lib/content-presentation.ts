import type { ContentNotice, FeedCard } from "@/lib/api";

export function productionLabel(value: FeedCard["productionType"]): string {
  if (value === "ai") return "AI 생성";
  if (value === "hybrid") return "AI 협업";
  return "일반 콘텐츠";
}

export function needsReview(notices: ContentNotice[], summary?: string | null): boolean {
  return notices.some((notice) =>
    notice.code?.toUpperCase() === "REVIEW_REQUIRED" ||
    notice.type?.toLowerCase() === "review_required" ||
    notice.message?.includes("검토 필요")
  ) || Boolean(summary?.startsWith("[개발용 미리보기 · 검토 필요]"));
}

export function publishedDate(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString("ko-KR");
}
