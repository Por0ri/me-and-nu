import { memberPageSize, type MemberDetail, type MemberQuery } from "../../lib/admin-members";

// Synthetic test data only. Never import this module from app/, components/ or lib/.
export const memberFixtures: MemberDetail[] = Array.from({ length: 31 }, (_, index) => ({
  id: `fixture-${index + 1}`, nickname: index < 5 ? "김하루" : `테스트회원${index + 1}`,
  maskedEmail: "haru***@demo.test", provider: index < 5 || index % 2 === 0 ? "kakao" : "naver",
  domain: "애니메이션", joinedAt: new Date(Date.UTC(2026, 10, 4, 23, 10) - index * 60_000).toISOString(), lastSeenAt: "2026-11-05T00:42:00Z",
  status: (["active", "withdrawn", "dormant", "active", "restricted"] as const)[index % 5],
  history: [
    { changedAt: "2026-11-10T05:32:00Z", before: "하루별", after: "김하루" },
    { changedAt: "2026-11-09T05:32:00Z", before: "하루", after: "하루별" },
    { changedAt: "2026-11-08T05:32:00Z", before: "하롱", after: "하루" },
    { changedAt: "2026-11-07T05:32:00Z", before: "하루짱", after: "하롱" },
  ],
  onboarding: { works: ["진격의 거인", "귀멸의 칼날", "나루토"], genres: ["액션", "판타지", "코미디", "로맨스", "초자연", "스릴러", "미스터리"], themes: ["성장", "학원", "비극"], contentTypes: ["설정과 세계관을 파고든다"] },
  notes: [{ id: "fixture-note", text: "닉네임을 자주 변경하는 사용자예요.", author: "테스트 관리자", createdAt: "2026-11-10T05:20:00Z" }], savedCount: 12, likedCount: 24,
}));

export function selectMemberFixtures(query: MemberQuery, records = memberFixtures) {
  const filtered = records.filter(item => (!query.status || item.status === query.status) && (!query.provider || item.provider === query.provider) &&
    (!query.q || `${item.nickname} ${item.maskedEmail}`.toLocaleLowerCase().includes(query.q.toLocaleLowerCase())))
    .sort((a, b) => (Date.parse(b.joinedAt!) - Date.parse(a.joinedAt!)) * (query.sort === "oldest" ? -1 : 1));
  return { total: filtered.length, items: filtered.slice((query.page - 1) * memberPageSize, query.page * memberPageSize) };
}
