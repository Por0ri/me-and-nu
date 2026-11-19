/**
 * 토론방 가짜 데이터 저장소. 브라우저 메모리에만 있어서 새로고침하면 처음 상태로 돌아간다.
 *
 * 함수 이름과 규칙은 백엔드 설계(docs/DEBATE_BACKEND_DESIGN_2026-11-19.md)와 같게 맞췄다.
 * 투표권 충전, 10표 달성 즉시 개설, 진영 비율, 채팅 속도 제한, 표 0개일 때만 주제 삭제가 모두 같은 규칙이다.
 * 실시간 스트림은 몇 초마다 다른 사람의 채팅·진영 변화를 흉내 낸다.
 * API가 생기면 client.ts에서 이 파일 대신 서버를 부르게 바꾼다.
 */
import { DEBATE_RULES as RULES } from "./rules";
import {
  DebateError,
  type CreateDebateInput,
  type DebateListQuery,
  type DebateMessage,
  type DebateResult,
  type DebateSearchResult,
  type DebateSideKey,
  type DebateStatus,
  type DebateStreamEvent,
  type DebateSummary,
  type DebateWorkOption,
  type VoteTickets,
} from "./types";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const REFILL = RULES.ticketRefillMinutes * MINUTE;

type StoredMessage = { messageId: number; side: DebateSideKey | null; nickname: string; body: string; createdAt: number; mine: boolean };
type StoredDebate = {
  debateId: number;
  title: string;
  description: string | null;
  sideA: string;
  sideB: string;
  workIds: number[];
  status: DebateStatus;
  voteCount: number;
  votingEndsAt: number;
  openedAt: number | null;
  closesAt: number | null;
  sideACount: number;
  sideBCount: number;
  messageCount: number;
  createdAt: number;
  mine: boolean;
  myVote: boolean;
  mySide: DebateSideKey | null;
  messages: StoredMessage[];
};

const WORKS: DebateWorkOption[] = [
  { entityId: 101, name: "원피스", aliases: ["ONE PIECE"] },
  { entityId: 102, name: "나루토", aliases: ["NARUTO", "나루토 질풍전"] },
  { entityId: 103, name: "진격의 거인", aliases: ["Attack on Titan", "진격거"] },
  { entityId: 104, name: "주술회전", aliases: ["Jujutsu Kaisen"] },
  { entityId: 105, name: "데스노트", aliases: ["DEATH NOTE"] },
  { entityId: 106, name: "귀멸의 칼날", aliases: ["Demon Slayer", "귀칼"] },
  { entityId: 107, name: "체인소 맨", aliases: ["Chainsaw Man"] },
  { entityId: 108, name: "하이큐!!", aliases: ["HAIKYU!!", "하이큐"] },
  { entityId: 109, name: "드래곤볼", aliases: ["DRAGON BALL"] },
  { entityId: 110, name: "블리치", aliases: ["BLEACH"] },
];

const LIVE_NAMES = ["애니덕후", "주말정주행", "떡밥수집가", "작화장인", "엔딩요정", "원작파", "애니입문러", "명장면러버", "정주행3회차"];
const LIVE_LINES: Record<DebateSideKey | "none", string[]> = {
  a: ["저는 {A} 편이에요. 근거가 더 탄탄해요.", "{A} 쪽 말이 계속 더 와닿네요.", "마지막 장면 다시 보면 {A} 쪽이 맞아요.", "{B} 편 말도 일리 있는데 그래도 {A}요."],
  b: ["{B} 쪽이 한 수 위라고 봐요.", "솔직히 {B} 편 근거가 더 설득력 있어요.", "{A} 편 얘기 들어봐도 저는 {B}예요.", "원작까지 보면 {B} 쪽이 맞다고 생각해요."],
  // 진영을 고르지 않고 채팅하는 사람
  none: ["둘 다 일리가 있어서 아직 못 고르겠어요.", "양쪽 얘기 더 듣고 정할게요.", "{A} 편이랑 {B} 편 다 설득력 있네요."],
};

let nextDebateId = 200;
let nextMessageId = 5000;
let debates: StoredDebate[] = [];
let tickets = { balance: RULES.ticketMax as number, refilledAt: Date.now() };
let mySends: number[] = [];

function stored(partial: Partial<StoredDebate> & Pick<StoredDebate, "debateId" | "title" | "sideA" | "sideB" | "workIds">, now: number): StoredDebate {
  return {
    description: null,
    status: "voting",
    voteCount: 0,
    votingEndsAt: now + RULES.votingDays * DAY,
    openedAt: null,
    closesAt: null,
    sideACount: 0,
    sideBCount: 0,
    messageCount: 0,
    createdAt: now,
    mine: false,
    myVote: false,
    mySide: null,
    messages: [],
    ...partial,
  };
}

function seedMessages(now: number, lines: [DebateSideKey | null, string, string][]): StoredMessage[] {
  return lines.map(([side, nickname, body], index) => ({
    messageId: nextMessageId++, side, nickname, body, mine: false,
    createdAt: now - (lines.length - index) * 4 * MINUTE,
  }));
}

/** 시안에 나온 예시로 처음 상태를 만든다. 내 투표권은 1개, 다음 충전은 18분 뒤. */
function seed(): void {
  const now = Date.now();
  nextDebateId = 200;
  nextMessageId = 5000;
  mySends = [];
  tickets = { balance: 1, refilledAt: now - 42 * MINUTE };
  const opened = (hoursAgo: number) => ({ status: "open" as const, openedAt: now - hoursAgo * HOUR, closesAt: now + (RULES.roomHours - hoursAgo) * HOUR, voteCount: RULES.voteThreshold, votingEndsAt: now + 3 * DAY });
  debates = [
    stored({
      debateId: 1, title: "나루토와 사스케, 최종 승자는?", sideA: "나루토", sideB: "사스케", workIds: [102], ...opened(17),
      description: "종말의 계곡 마지막 결투 기준으로 이야기해요.", sideACount: 5, sideBCount: 3, createdAt: now - 4 * DAY,
      messages: seedMessages(now, [
        ["a", "닌자왕", "나는 나루토가 이긴다고 봐요. 결국 사스케도 나루토의 신념과 끈질김을 인정했잖아요."],
        ["b", "우치하팬", "그래도 사스케의 재능과 전투 센스는 정말 대단했어요. 마지막까지 예측이 어려워요!"],
        ["b", "사스케최고", "사스케가 이긴다고 생각해요. 타고난 재능, 전투 센스, 그리고 후반부 성장까지 압도적이었죠."],
        ["a", "나루토존버", "하지만 결국 나루토는 사람의 마음을 바꾸는 힘이 있어요. 그게 진짜 승자의 모습 아닐까요?"],
        [null, "코노하마을주민", "개인적으로 두 사람이 서로를 이해하고 화해한 게 진짜 결말이라고 생각해요. 승부를 넘어서요!"],
        ["a", "필승나루토", "저도 나루토 쪽이에요! 단순한 전투 승리가 아니라 사스케의 마음까지 움직인 게 진정한 승리라고 봅니다."],
      ]),
    }, now),
    stored({
      debateId: 2, title: "루피는 해적왕이 될 수 있을까?", sideA: "된다", sideB: "못 된다", workIds: [101], ...opened(4),
      sideACount: 4, sideBCount: 2, createdAt: now - 3 * DAY,
      messages: seedMessages(now, [
        ["a", "밀짚모자", "제목부터 원피스잖아요. 된다 편이에요."],
        ["b", "현실주의자", "꿈은 이뤄지지만 해적왕은 다른 의미일 수도 있어요. 못 된다 쪽입니다."],
      ]),
    }, now),
    stored({ debateId: 3, title: "에렌과 리바이가 싸우면 누가 이길까?", sideA: "에렌", sideB: "리바이", workIds: [103], voteCount: 8, votingEndsAt: now + 5 * DAY + 3 * HOUR, myVote: true, createdAt: now - 2 * DAY }, now),
    // 9/10표: 내가 10번째 표를 넣으면 바로 토론방이 열리는 걸 볼 수 있게 아직 투표하지 않은 상태로 둔다.
    stored({ debateId: 4, title: "데스노트의 라이트, 그는 악인일까?", sideA: "악인이다", sideB: "아니다", workIds: [105], voteCount: 9, votingEndsAt: now + 20 * HOUR, createdAt: now - 6 * DAY }, now),
    stored({ debateId: 5, title: "고죠 사토루는 정말 최강일까?", sideA: "최강이다", sideB: "아니다", workIds: [104], voteCount: 7, votingEndsAt: now + 6 * DAY, myVote: true, createdAt: now - DAY }, now),
    stored({ debateId: 6, title: "탄지로와 렌고쿠, 누가 더 강할까?", sideA: "탄지로", sideB: "렌고쿠", workIds: [106], voteCount: 4, votingEndsAt: now + 4 * DAY, createdAt: now - 3 * DAY }, now),
    stored({ debateId: 7, title: "덴지는 히어로일까?", sideA: "히어로다", sideB: "아니다", workIds: [107], voteCount: 2, votingEndsAt: now + 6 * DAY + 5 * HOUR, createdAt: now - 18 * HOUR }, now),
    stored({ debateId: 8, title: "히나타와 카게야마, 더 대단한 쪽은?", sideA: "히나타", sideB: "카게야마", workIds: [108], voteCount: 1, votingEndsAt: now + 6 * DAY + 20 * HOUR, createdAt: now - 4 * HOUR }, now),
    // 지난 토론방: 종료 3개, 소멸 2개
    stored({ debateId: 11, title: "원피스, 최종 보물은 무엇일까?", sideA: "실제 보물", sideB: "동료와의 우정", workIds: [101], status: "closed", voteCount: 10, votingEndsAt: now - 6 * DAY, openedAt: now - 5 * DAY, closesAt: now - 3 * DAY, sideACount: 7, sideBCount: 11, messageCount: 214, createdAt: now - 9 * DAY }, now),
    stored({ debateId: 12, title: "카카시와 이타치, 누가 더 강할까?", sideA: "카카시", sideB: "이타치", workIds: [102], status: "closed", voteCount: 10, votingEndsAt: now - 8 * DAY, openedAt: now - 7 * DAY, closesAt: now - 5 * DAY, sideACount: 9, sideBCount: 9, messageCount: 156, createdAt: now - 12 * DAY }, now),
    stored({ debateId: 13, title: "엘과 라이트, 진짜 천재는 누구?", sideA: "엘", sideB: "라이트", workIds: [105], status: "closed", voteCount: 10, votingEndsAt: now - 10 * DAY, openedAt: now - 9 * DAY, closesAt: now - 7 * DAY, sideACount: 12, sideBCount: 8, messageCount: 301, createdAt: now - 15 * DAY }, now),
    stored({ debateId: 14, title: "고죠와 스쿠나, 전성기 대결 승자는?", sideA: "고죠", sideB: "스쿠나", workIds: [104], status: "expired", voteCount: 6, votingEndsAt: now - 2 * DAY, createdAt: now - 9 * DAY }, now),
    stored({ debateId: 15, title: "베지터는 손오공을 넘었을까?", sideA: "넘었다", sideB: "아니다", workIds: [109], status: "expired", voteCount: 3, votingEndsAt: now - 4 * DAY, createdAt: now - 11 * DAY }, now),
  ];
  for (const debate of debates) debate.messageCount = Math.max(debate.messageCount, debate.messages.length);
}
seed();

/** 처음 상태로 되돌린다 (화면의 '처음 상태로' 버튼). */
export function resetMockDebates(): void {
  seed();
}

function wait<T>(value: T, ms = 150): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

function fail(code: string, message: string): never {
  throw new DebateError(code, message);
}

function find(debateId: number): StoredDebate {
  return debates.find((debate) => debate.debateId === debateId) ?? fail("DEBATE_NOT_FOUND", "토론을 찾을 수 없어요.");
}

/** 저장된 상태가 늦어도 마감 시각이 지나면 끝난 것으로 본다 (설계 4장). */
function statusOf(debate: StoredDebate, now: number): DebateStatus {
  if (debate.status === "voting" && now >= debate.votingEndsAt) return "expired";
  if (debate.status === "open" && debate.closesAt !== null && now >= debate.closesAt) return "closed";
  return debate.status;
}

function percents(a: number, b: number): [number | null, number | null] {
  if (a + b === 0) return [null, null];
  const percentA = Math.round((a / (a + b)) * 100);
  return [percentA, 100 - percentA];
}

function resultOf(debate: StoredDebate): DebateResult {
  if (debate.sideACount + debate.sideBCount === 0) return "none";
  if (debate.sideACount === debate.sideBCount) return "draw";
  return debate.sideACount > debate.sideBCount ? "a" : "b";
}

function iso(time: number | null): string | null {
  return time === null ? null : new Date(time).toISOString();
}

function summary(debate: StoredDebate, now = Date.now()): DebateSummary {
  const status = statusOf(debate, now);
  const [percentA, percentB] = percents(debate.sideACount, debate.sideBCount);
  return {
    debateId: debate.debateId,
    title: debate.title,
    description: debate.description,
    sideA: { label: debate.sideA, count: debate.sideACount, percent: percentA },
    sideB: { label: debate.sideB, count: debate.sideBCount, percent: percentB },
    works: debate.workIds.map((id) => WORKS.find((work) => work.entityId === id)).filter((work) => work !== undefined)
      .map(({ entityId, name }) => ({ entityId, name })),
    status,
    voteCount: debate.voteCount,
    voteThreshold: RULES.voteThreshold,
    votingEndsAt: new Date(debate.votingEndsAt).toISOString(),
    openedAt: iso(debate.openedAt),
    closesAt: iso(debate.closesAt),
    participantCount: debate.sideACount + debate.sideBCount,
    messageCount: debate.messageCount,
    result: status === "closed" ? resultOf(debate) : null,
    myVote: debate.myVote,
    mySide: debate.mySide,
    isMine: debate.mine,
    createdAt: new Date(debate.createdAt).toISOString(),
  };
}

function message(stored: StoredMessage): DebateMessage {
  const { messageId, side, nickname, body, createdAt, mine } = stored;
  return { messageId, side, nickname, body, createdAt: new Date(createdAt).toISOString(), isMine: mine };
}

/** 투표권: 매시간 도는 작업 없이 볼 때 계산한다 (설계 6.2). */
function currentTickets(now: number): { balance: number; anchor: number } {
  if (tickets.balance >= RULES.ticketMax) return { balance: RULES.ticketMax, anchor: now };
  const refills = Math.floor((now - tickets.refilledAt) / REFILL);
  const balance = Math.min(RULES.ticketMax, tickets.balance + refills);
  return { balance, anchor: tickets.refilledAt + refills * REFILL };
}

function ticketView(now: number): VoteTickets {
  const { balance, anchor } = currentTickets(now);
  return { balance, max: RULES.ticketMax, nextRefillAt: balance < RULES.ticketMax ? new Date(anchor + REFILL).toISOString() : null };
}

function endedAt(debate: StoredDebate): number {
  return debate.closesAt ?? debate.votingEndsAt;
}

function matches(debate: StoredDebate, text: string): boolean {
  const needle = text.trim().toLowerCase();
  if (!needle) return true;
  const works = WORKS.filter((work) => debate.workIds.includes(work.entityId)).flatMap((work) => [work.name, ...work.aliases]);
  return [debate.title, debate.sideA, debate.sideB, ...works].some((value) => value.toLowerCase().includes(needle));
}

export async function listDebates(query: DebateListQuery): Promise<DebateSummary[]> {
  const now = Date.now();
  let rows = debates.filter((debate) => query.entityId === undefined || debate.workIds.includes(query.entityId));
  if (query.status === "voting") {
    rows = rows.filter((debate) => statusOf(debate, now) === "voting");
    rows.sort(query.sort === "votes"
      ? (left, right) => right.voteCount - left.voteCount || left.votingEndsAt - right.votingEndsAt
      : (left, right) => right.createdAt - left.createdAt);
  } else if (query.status === "open") {
    rows = rows.filter((debate) => statusOf(debate, now) === "open").sort((left, right) => (left.closesAt ?? 0) - (right.closesAt ?? 0));
  } else {
    const wanted = query.result ?? "all";
    rows = rows.filter((debate) => {
      const status = statusOf(debate, now);
      return (status === "closed" || status === "expired") && (wanted === "all" || wanted === status) && matches(debate, query.q ?? "");
    }).sort((left, right) => endedAt(right) - endedAt(left));
  }
  return wait(rows.map((debate) => summary(debate, now)));
}

export async function getDebate(debateId: number): Promise<DebateSummary> {
  return wait(summary(find(debateId)));
}

export async function getVoteTickets(): Promise<VoteTickets> {
  return wait(ticketView(Date.now()));
}

export async function voteDebate(debateId: number): Promise<{ debate: DebateSummary; tickets: VoteTickets; opened: boolean }> {
  const now = Date.now();
  const debate = find(debateId);
  if (statusOf(debate, now) !== "voting") fail("DEBATE_NOT_VOTING", "투표가 끝난 주제예요.");
  if (debate.myVote) return wait({ debate: summary(debate, now), tickets: ticketView(now), opened: false });
  const { balance, anchor } = currentTickets(now);
  if (balance === 0) fail("DEBATE_VOTE_TICKETS_EMPTY", `투표권이 없어요. ${Math.max(1, Math.ceil((anchor + REFILL - now) / MINUTE))}분 뒤에 1개 충전돼요.`);
  tickets = { balance: balance - 1, refilledAt: balance === RULES.ticketMax ? now : anchor };
  debate.myVote = true;
  debate.voteCount += 1;
  const opened = debate.voteCount >= RULES.voteThreshold;
  if (opened) {
    // 10번째 표가 들어온 순간 바로 연다. 실제 서버는 이때 투표한 사람과 작성자에게 알림을 만든다.
    debate.status = "open";
    debate.openedAt = now;
    debate.closesAt = now + RULES.roomHours * HOUR;
  }
  return wait({ debate: summary(debate, now), tickets: ticketView(now), opened });
}

export async function cancelVote(debateId: number): Promise<{ debate: DebateSummary; tickets: VoteTickets }> {
  const now = Date.now();
  const debate = find(debateId);
  if (statusOf(debate, now) !== "voting") fail("DEBATE_NOT_VOTING", "이미 열렸거나 끝난 주제라 취소할 수 없어요.");
  if (debate.myVote) {
    debate.myVote = false;
    debate.voteCount -= 1;
    // 취소하면 1개 즉시 돌려준다. 이미 가득이면 더 늘지 않는다.
    const { balance, anchor } = currentTickets(now);
    tickets = { balance: Math.min(RULES.ticketMax, balance + 1), refilledAt: anchor };
  }
  return wait({ debate: summary(debate, now), tickets: ticketView(now) });
}

export async function listMyDebates(): Promise<DebateSummary[]> {
  const now = Date.now();
  return wait(debates.filter((debate) => debate.mine).sort((left, right) => right.createdAt - left.createdAt).map((debate) => summary(debate, now)));
}

/** 내가 투표한 주제. 아직 투표 중인 것을 먼저, 그다음 열린 것 */
export async function listMyVotes(): Promise<DebateSummary[]> {
  const now = Date.now();
  const rank = (debate: StoredDebate) => (statusOf(debate, now) === "voting" ? 0 : 1);
  return wait(debates.filter((debate) => debate.myVote && statusOf(debate, now) !== "expired")
    .sort((left, right) => rank(left) - rank(right) || left.votingEndsAt - right.votingEndsAt)
    .map((debate) => summary(debate, now)));
}

export async function createDebate(input: CreateDebateInput): Promise<DebateSummary> {
  const now = Date.now();
  const title = input.title.trim();
  const description = input.description.trim();
  const sideA = input.sideA.trim();
  const sideB = input.sideB.trim();
  if (!title || title.length > RULES.titleMax) fail("VALIDATION_ERROR", `주제 제목은 1~${RULES.titleMax}자로 적어주세요.`);
  if (description.length > RULES.descriptionMax) fail("VALIDATION_ERROR", `설명은 ${RULES.descriptionMax}자까지 적을 수 있어요.`);
  if (!sideA || !sideB || sideA.length > RULES.sideLabelMax || sideB.length > RULES.sideLabelMax) {
    fail("VALIDATION_ERROR", `양쪽 선택지를 1~${RULES.sideLabelMax}자로 적어주세요.`);
  }
  if (sideA.toLowerCase() === sideB.toLowerCase()) fail("VALIDATION_ERROR", "양쪽 선택지 이름이 달라야 해요.");
  const workIds = [...new Set(input.workIds)];
  if (workIds.length > RULES.worksMax || workIds.some((id) => !WORKS.some((work) => work.entityId === id))) {
    fail("VALIDATION_ERROR", `작품은 목록에서 ${RULES.worksMax}개까지 고를 수 있어요.`);
  }
  const active = debates.filter((debate) => debate.mine && statusOf(debate, now) === "voting").length;
  if (active >= RULES.activePerUser) fail("DEBATE_ACTIVE_LIMIT", "투표 중인 내 주제가 이미 있어요. 그 주제가 열리거나 소멸되면 새로 올릴 수 있어요.");
  const debate = stored({ debateId: nextDebateId++, title, description: description || null, sideA, sideB, workIds, mine: true, createdAt: now }, now);
  debates.push(debate);
  return wait(summary(debate, now), 250);
}

export async function deleteDebate(debateId: number): Promise<void> {
  const now = Date.now();
  const debate = find(debateId);
  if (!debate.mine) fail("DEBATE_NOT_FOUND", "토론을 찾을 수 없어요.");
  if (statusOf(debate, now) !== "voting" || debate.voteCount > 0) {
    fail("DEBATE_DELETE_NOT_ALLOWED", "표를 받은 주제는 지울 수 없어요. 다른 사람의 투표권이 걸려 있어요.");
  }
  debates = debates.filter((row) => row.debateId !== debateId);
  return wait(undefined);
}

export async function chooseSide(debateId: number, side: DebateSideKey): Promise<DebateSummary> {
  const now = Date.now();
  const debate = find(debateId);
  if (statusOf(debate, now) !== "open") fail("DEBATE_NOT_OPEN", "진행 중인 토론방이 아니에요.");
  if (debate.mySide !== side) {
    if (debate.mySide === "a") debate.sideACount -= 1;
    if (debate.mySide === "b") debate.sideBCount -= 1;
    if (side === "a") debate.sideACount += 1;
    else debate.sideBCount += 1;
    debate.mySide = side;
  }
  return wait(summary(debate, now), 100);
}

export async function listMessages(debateId: number): Promise<DebateMessage[]> {
  return wait(find(debateId).messages.slice(-50).map(message));
}

export async function sendMessage(debateId: number, body: string): Promise<DebateMessage> {
  const now = Date.now();
  const debate = find(debateId);
  const text = body.trim();
  // 진영 선택과 채팅은 별개라 진영을 고르지 않아도 보낼 수 있다.
  if (statusOf(debate, now) !== "open") fail("DEBATE_NOT_OPEN", "진행 중인 토론방이 아니에요.");
  if (!text || text.length > RULES.messageMax) fail("VALIDATION_ERROR", `메시지는 1~${RULES.messageMax}자로 보내주세요.`);
  const windowStart = now - RULES.messageRateWindowSeconds * 1000;
  mySends = mySends.filter((time) => time > windowStart);
  if (mySends.length >= RULES.messageRateCount) {
    const retry = Math.max(1, Math.ceil((mySends[0] + RULES.messageRateWindowSeconds * 1000 - now) / 1000));
    fail("DEBATE_MESSAGE_RATE_LIMITED", `너무 빨라요. ${retry}초 뒤에 다시 보내주세요.`);
  }
  mySends.push(now);
  const stored: StoredMessage = { messageId: nextMessageId++, side: debate.mySide, nickname: "나", body: text, createdAt: now, mine: true };
  debate.messages.push(stored);
  debate.messageCount += 1;
  return wait(message(stored), 80);
}

/** 주제 올리기의 작품 고르기 (설계 D-17) */
export async function searchWorks(text: string): Promise<DebateWorkOption[]> {
  const needle = text.trim().toLowerCase();
  const rows = needle ? WORKS.filter((work) => [work.name, ...work.aliases].some((value) => value.toLowerCase().includes(needle))) : WORKS;
  return wait(rows.slice(0, 8), 80);
}

/** 검색 입력창 하나: 작품 후보와 토론 목록을 같이 준다 (설계 D-16). 검색 기록 옵션은 가짜 데이터에선 무시한다. */
export async function searchDebates(text: string, _options: { record?: boolean; pickedEntityId?: number } = {}): Promise<DebateSearchResult> {
  const now = Date.now();
  const needle = text.trim().toLowerCase();
  if (!needle) return wait({ works: [], debates: [] });
  const works = WORKS.filter((work) => [work.name, ...work.aliases].some((value) => value.toLowerCase().includes(needle))).slice(0, 5);
  const rank: Record<DebateStatus, number> = { open: 0, voting: 1, closed: 2, expired: 3 };
  const found = debates.filter((debate) => matches(debate, needle))
    .sort((left, right) => rank[statusOf(left, now)] - rank[statusOf(right, now)] || right.createdAt - left.createdAt)
    .map((debate) => summary(debate, now));
  return wait({ works, debates: found }, 100);
}

/** 주제 올리기 화면의 '비슷한 주제'. 제목의 두 글자 이상 단어가 겹치는 토론 */
export async function similarDebates(title: string): Promise<DebateSummary[]> {
  const now = Date.now();
  const words = title.split(/[\s,?!.·]+/).map((word) => word.trim().toLowerCase()).filter((word) => word.length >= 2);
  if (words.length === 0) return wait([]);
  const rows = debates.filter((debate) => words.some((word) => matches(debate, word))).slice(0, 3);
  return wait(rows.map((debate) => summary(debate, now)), 100);
}

/**
 * 실시간 스트림 흉내. 몇 초마다 다른 사람의 채팅이나 진영 변화를 보내고, 방이 끝나면 closed를 보낸다.
 * 실제로는 SSE(GET /debates/{id}/stream)가 같은 이벤트를 보낸다.
 */
export function subscribeDebate(debateId: number, onEvent: (event: DebateStreamEvent) => void): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const pick = <T,>(items: readonly T[]): T => items[Math.floor(Math.random() * items.length)];
  const tick = () => {
    if (stopped) return;
    const now = Date.now();
    const debate = debates.find((row) => row.debateId === debateId);
    if (!debate) return;
    if (statusOf(debate, now) !== "open") {
      onEvent({ type: "closed", debate: summary(debate, now) });
      return;
    }
    if (Math.random() < 0.3) {
      // 새 사람이 진영을 고르거나, 누군가 진영을 바꾼다.
      const joinA = Math.random() < 0.5;
      if (Math.random() < 0.6 || (joinA ? debate.sideBCount : debate.sideACount) === 0) {
        if (joinA) debate.sideACount += 1;
        else debate.sideBCount += 1;
      } else if (joinA) {
        debate.sideBCount -= 1;
        debate.sideACount += 1;
      } else {
        debate.sideACount -= 1;
        debate.sideBCount += 1;
      }
      const view = summary(debate, now);
      onEvent({ type: "sides", sideA: view.sideA, sideB: view.sideB, participantCount: view.participantCount });
    } else {
      const total = debate.sideACount + debate.sideBCount;
      const side: DebateSideKey | null = Math.random() < 0.2 ? null
        : Math.random() < (total ? debate.sideACount / total : 0.5) ? "a" : "b";
      const body = pick(LIVE_LINES[side ?? "none"]).replaceAll("{A}", debate.sideA).replaceAll("{B}", debate.sideB);
      const stored: StoredMessage = { messageId: nextMessageId++, side, nickname: pick(LIVE_NAMES), body, createdAt: now, mine: false };
      debate.messages.push(stored);
      debate.messageCount += 1;
      onEvent({ type: "chat", message: message(stored) });
    }
    timer = setTimeout(tick, 4000 + Math.random() * 5000);
  };
  timer = setTimeout(tick, 2500);
  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}
