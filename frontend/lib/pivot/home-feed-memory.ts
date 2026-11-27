import type { ServiceBookmarkControls } from "./bookmark-service";
import type { HomeReturnSnapshot } from "./detail-navigation";
import type { HomeCardModel, HomePageLoader, HomeViewModel } from "./home";
import type { ReactionControls } from "./reaction-service";

/*
 * TALK(토론방)에 다녀오는 동안 홈 피드를 기억해 둔다 (2026-11-25 사용자 요청: 돌아오면 보던 피드 · 자리부터 이어서 본다).
 * TALK 은 다른 페이지라 홈 화면이 통째로 내려가서, 기억해 두지 않으면 돌아올 때 로딩부터 새 피드 1페이지로 다시 시작한다.
 *
 * - 이 탭의 메모리에만 둔다. 새로고침하면 사라져 새 피드를 받는다.
 * - 1시간 안에 돌아올 때만 이어서 보여 준다 (사용자 결정). 더 오래 있다 오면 새 피드.
 * - 같은 로그인(회원 + 세션)일 때만 꺼낸다. 다른 계정의 피드가 보이면 안 된다.
 * - TALK 으로 갈 때만 기억한다. 취향 수정 같은 다른 곳에 다녀오면 취향이 바뀌었을 수 있어서 지금처럼 새로 불러온다.
 * - 꺼내기(peek)와 지우기(forget)를 나눈다: 개발 모드(StrictMode)는 화면을 두 번 그려서, 꺼내면서 바로 지우면 두 번째에 비어 버린다.
 */

export const HOME_FEED_KEEP_MS = 60 * 60_000;
/** TALK 버튼을 누를 때 홈에 알리는 이벤트 (home-screen.tsx HomeNavigation → home-detail-experience.tsx) */
export const LEAVE_HOME_FOR_TALK = "pivot:leave-home-for-talk";

/** 불러 둔 카드와 다음 페이지를 이어서 받을 함수 */
export type KeptHomeFeed = { home: HomeViewModel; nextPage?: HomePageLoader };
export type KeptHome = { feed: KeptHomeFeed; snapshot: HomeReturnSnapshot };

let kept: (KeptHome & { owner: string; savedAt: number }) | null = null;

export function keepHomeFeed(owner: string, entry: KeptHome, now = Date.now()): void {
  kept = { ...entry, owner, savedAt: now };
}

/** 같은 로그인이고 1시간이 안 지났으면 기억해 둔 피드를 돌려준다. 지우지는 않는다 (forgetHomeFeed). */
export function peekHomeFeed(owner: string, now = Date.now()): KeptHome | null {
  if (!kept) return null;
  if (kept.owner !== owner || now - kept.savedAt > HOME_FEED_KEEP_MS || now < kept.savedAt) {
    kept = null;
    return null;
  }
  return { feed: kept.feed, snapshot: kept.snapshot };
}

export function forgetHomeFeed(): void {
  kept = null;
}

/**
 * 기억할 피드의 카드에 지금 화면에 보이는 저장 · 좋아요 · 선호를 넣는다.
 * 카드에 든 값은 피드를 받을 때의 값이고, 그 뒤에 누른 것은 홈 화면의 기억(저장 · 반응)에만 있다.
 * 돌아오면 그 기억이 새로 만들어져 비어 있어서, 넣어 두지 않으면 누르기 전 모습으로 보인다.
 * 아직 끝나지 않은 요청이 있으면 결과를 몰라서 null (기억하지 않는다 → 돌아오면 피드를 새로 받는다).
 */
export function withShownReactions(feed: KeptHomeFeed, shown: {
  bookmarks: Pick<ServiceBookmarkControls, "has" | "isPending">;
  reactions?: Pick<ReactionControls, "value" | "isPending">;
}): KeptHomeFeed | null {
  const { bookmarks, reactions } = shown;
  const cards: HomeCardModel[] = [];
  try {
    for (const card of feed.home.cards) {
      if (card.topicId === undefined) { cards.push(card); continue; }
      const target = { postId: card.id, contentId: card.detailContentId, topicId: card.topicId };
      if (bookmarks.isPending(target) || reactions?.isPending(target, "like") || reactions?.isPending(target, "preference")) return null;
      const reaction = reactions?.value(target, { liked: card.liked, preference: card.preference });
      cards.push({ ...card, ...reaction, saved: bookmarks.has(target, card.saved) });
    }
  } catch {
    return null; // 글 번호를 알 수 없는 카드: 기억하지 않고 새로 받는 편이 안전하다
  }
  return { ...feed, home: { ...feed.home, cards } };
}
