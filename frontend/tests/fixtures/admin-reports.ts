import { reportPageSize, type ReportContent, type ReportPage, type ReportQuery, type ReportRecord, type ReportStatistics } from "../../lib/admin-reports";

// Figma samples and synthetic edge cases. Imported only by the isolated test checkout.
const content: ReportContent = {
  contentId: "1", productionType: "ai",
  title: "사이버펑크: 엣지러너 2\n화면은 번쩍, 평은 갈린다", subtitle: "리뷰어들이 짚은 매력과 아쉬움,\n팬 시선으로 골라봤어.", category: "리뷰 큐레이션",
  coverUrl: "/admin/report-fixture-cover.png",
  body: "《사이버펑크: 엣지러너 2》를 둘러싼 리뷰를 읽다 보면, 같은 나이트 시티를 보고도 감상이 꽤 다른 레인으로 달린다는 게 보여. 한 리뷰어는 애니메이션만의 표현이 돋보이고 화면이 내내 멋있다며 연출을 극찬했다. 눈이 먼저 반응하는 작품이라는 말, 팬 마음으로는 고개가 끄덕여지는 감상이지.\n\n반면 시즌 1을 좋아해 후속작을 기다렸던 리뷰어는 장면 전환이 잦고 컷 하나하나의 길이가 짧아, 분위기를 더 천천히 음미하고 싶었다고 했다. 속도감이 매력일 수도 있지만, 그 도시의 공기를 오래 들이마시고 싶은 관객에게는 아쉬움으로 남은 셈이다.\n\n로만을 보는 시선도 엇갈린다. 한 리뷰어에게 그는 주변 인물들이 뒤엉킨 가운데 그나마 제정신인 사람처럼 보였고, 결말도 좋았다고 한다. 다른 리뷰어는 이야기의 큰 줄기는 좋게 보면서도 로만의 행동에 답답함을 느꼈다. 결국 영상의 번쩍임과 인물들의 선택을 어떻게 받아들이느냐에 따라 감상이 갈리는 작품. 호평과 아쉬움이 함께 있다는 사실 자체가, 리뷰를 읽고 내 감상을 더해 보고 싶게 만드는 지점이야.",
  source: { title: "Filmarks Anime Ratings ? アニメ『サイバーパンク: エッジランナーズ2』の感想・レビュー[1808件] | Filmarks", url: "https://filmarks.com/animes/3610/6583" }, aiGenerated: true,
};
const first: ReportRecord = { id: "fixture-report-1", number: "R-001", recordId: "1", recordType: "report", title: content.title, reasonCode: "incorrect_info", rawStatus: "received", updatedAt: "2026-11-05T00:42:00Z", answer: null, answerAvailability: "unavailable", kind: "content", reason: "잘못된 정보", status: "waiting", receivedAt: "2026-11-05T00:42:00Z", target: "김하루",
  message: "게시물에 포함된 발매 정보가 실제 내용과 다르게 표시되어 있어요. 사실 확인을 요청합니다.", content };
export const reportFixtures: ReportRecord[] = [
  first,
  { ...first, id: "fixture-report-2", number: "R-002", reason: "스포일러", status: "reviewing", rawStatus: "reviewing" },
  { ...first, id: "fixture-report-3", number: "R-003", reason: "저작권·권리 침해", content: null },
  { ...first, id: "fixture-report-4", number: "R-004", kind: "service", reason: null, message: "애니 말고 다른 관심분야는 언제 열리나요?", status: "completed", rawStatus: "answered", content: null },
  { ...first, id: "fixture-report-5", number: "R-005", kind: "bug", reason: null, message: "버그가 너무 심해요.", status: "completed", rawStatus: "closed", content: null },
  ...Array.from({ length: 140 }, (_, index): ReportRecord => ({ ...first, id: `fixture-report-${index + 6}`, number: `R-${String(index + 6).padStart(3, "0")}`,
    status: index < 10 ? "waiting" : index < 14 ? "reviewing" : "completed", content: null })),
  ...["2026-10-15T00:00:00Z", "2026-09-01T00:00:00Z", "2026-06-01T00:00:00Z"].map((receivedAt, index): ReportRecord => ({ ...first, id: `older-${index}`, number: `OLD-${index}`, receivedAt, content: null })),
];

// Test clock and period rules are deliberately isolated; they do not define an API contract.
const fixtureNow = Date.parse("2026-11-08T00:00:00+09:00");
function inPeriod(item: ReportRecord, period: ReportQuery["period"]): boolean {
  const cutoff = new Date(fixtureNow);
  if (period === "7d" || period === "30d") cutoff.setUTCDate(cutoff.getUTCDate() - (period === "7d" ? 7 : 30));
  else cutoff.setUTCMonth(cutoff.getUTCMonth() - (period === "3m" ? 3 : 6));
  const date = item.receivedAt ? Date.parse(item.receivedAt) : NaN;
  return date >= cutoff.getTime() && date <= fixtureNow;
}
export function selectReportFixtures(query: ReportQuery, records = reportFixtures): ReportPage {
  const search = query.q.toLocaleLowerCase("ko-KR");
  const filtered = records.filter(item => inPeriod(item, query.period) && (!query.status || item.status === query.status) &&
    (!search || `${item.number} ${item.content?.title ?? ""}`.toLocaleLowerCase("ko-KR").includes(search)));
  const counts = reportFixtureStatistics(query.period);
  return { total: filtered.length, page: query.page, pageSize: reportPageSize, periodStart: "2026-11-01T00:00:00Z", periodEnd: "2026-11-08T00:00:00Z",
    statistics: { all: counts.all.count, waiting: counts.waiting.count, reviewing: counts.reviewing.count, completed: counts.completed.count }, items: filtered.slice((query.page - 1) * reportPageSize, query.page * reportPageSize) };
}
export function reportFixtureStatistics(period: ReportQuery["period"]): ReportStatistics {
  const items = reportFixtures.filter(item => inPeriod(item, period));
  return { all: { count: items.length, change: period === "7d" ? 6 : null }, waiting: { count: items.filter(item => item.status === "waiting").length, change: period === "7d" ? 3 : null },
    reviewing: { count: items.filter(item => item.status === "reviewing").length, change: period === "7d" ? 1 : null }, completed: { count: items.filter(item => item.status === "completed").length, change: period === "7d" ? 8 : null } };
}
