import { noticePageSize, type NoticePage, type NoticeQuery, type NoticeRecord, type NoticeStatistics } from "../../lib/admin-notices";

// Test-only Figma examples. No production imports or transport.
export const noticeFixtures: NoticeRecord[] = Array.from({ length: 32 }, (_, index) => ({
  id: `fixture-notice-${index + 1}`,
  title: index === 1 ? "[시스템 점검] 11월 12일 새벽 2시~4시" : index < 7 ? "[공지] me;nu 업데이트 안내" : `테스트 공지 ${index + 1}`,
  body: index === 1 ? "안녕하세요. me;nu입니다.\n\n더 안정적인 서비스 제공을 위해\n11월 12일 (목) 새벽 2시부터 4시까지 서비스 점검이 진행됩니다.\n\n점검 중에는 서비스 접속이 어렵고, 일부 기능이\n정상적으로 동작하지 않을 수 있어요.\n이용에 불편을 드려 죄송하며, 더 나은 서비스로 보답하겠습니다." : index === 2 ? null : "테스트 전용 공지 본문입니다.",
  status: [2, 4, 5, 6].includes(index) ? "scheduled" : "published",
  publishedAt: "2026-11-05T01:27:00Z", views: index === 2 ? null : 8421,
}));
export const noticeFixtureStatistics: NoticeStatistics = { all: { count: 32, change: 2 }, published: { count: 28, change: 2 }, scheduled: { count: 4, change: 0 } };
export function selectNoticeFixtures(query: NoticeQuery): NoticePage {
  const filtered = noticeFixtures.filter(item => (!query.status || item.status === query.status) && item.title.toLocaleLowerCase("ko-KR").includes(query.q.trim().toLocaleLowerCase("ko-KR")));
  return { items: filtered.slice((query.page - 1) * noticePageSize, query.page * noticePageSize), total: filtered.length };
}
