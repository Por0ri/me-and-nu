"use client";

import { useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import type { HomeDataSource, HomeDetailTarget } from "@/lib/pivot/home";
import type { DetailDataSource } from "@/lib/pivot/detail";
import { detailHref, readHomeReturn, type HomeReturnSnapshot } from "@/lib/pivot/detail-navigation";
import { forgetHomeFeed, keepHomeFeed, LEAVE_HOME_FOR_TALK, peekHomeFeed, withShownReactions, type KeptHomeFeed } from "@/lib/pivot/home-feed-memory";
import type { MyPageDataSource } from "@/lib/pivot/my-page";
import { pendingMyPageSource } from "@/lib/pivot/my-page-service";
import type { DemoBookmarkControls, SavedDataSource } from "@/lib/pivot/saved";
import { pendingSavedSource } from "@/lib/pivot/saved-service";
import { createServiceBookmarkControls } from "@/lib/pivot/bookmark-service";
import { readSavedReturn, type SavedReturnSnapshot } from "@/lib/pivot/saved-navigation";
import { HomeController } from "./home-controller";
import { homeScrollY, scrollHomeTo } from "./home-scroll";
import { DetailController } from "./detail-controller";
import { MyPageController } from "./my-page-controller";
import { SavedController } from "./saved-controller";
import { MyActivityController } from "./my-activity-controller";
import { InquiryController } from "./inquiry-controller";
import type { InquirySource } from "@/lib/pivot/inquiry";
import type { ReportSource } from "@/lib/pivot/report";
import { activityTab, type ActivityDataSource, type ActivityTab } from "@/lib/pivot/my-activity";
import { InteractionViewContext, useInteractions } from "./interaction-context";
import { createDemoNotificationInbox, newPostTarget, type InboxNotification, type NotificationInboxSource } from "@/lib/pivot/notification-inbox";
import { NotificationInbox } from "./notification-inbox";
import { NewNoticeContext } from "./home-screen";
import { RESTORE_HOME_TRACKS, type HomeTrackRestore } from "./windowed-home-feed";

const noSubscribe = () => () => {};
const noSnapshot = () => 0;

export function HomeDetailExperience({ homeSource, detailSource, mySource = pendingMyPageSource, savedSource = pendingSavedSource, activitySource, inquirySource, reportSource, notificationSource, demoBookmarks, onRestart, onLogout, onWithdraw, skipEntryLoading, feedOwner }: {
  homeSource: HomeDataSource; detailSource: DetailDataSource; mySource?: MyPageDataSource; savedSource?: SavedDataSource;
  activitySource?: ActivityDataSource;
  inquirySource?: InquirySource;
  reportSource?: ReportSource;
  notificationSource?: NotificationInboxSource;
  skipEntryLoading?: boolean;
  demoBookmarks?: DemoBookmarkControls; onRestart?: () => void; onLogout?: () => Promise<void>; onWithdraw?: () => Promise<void>;
  /** 지금 로그인(회원 + 세션). 있으면 TALK 에 다녀오는 동안 피드를 기억해 두고 이어서 보여 준다 (home-feed-memory.ts) */
  feedOwner?: string;
}) {
  const params = useSearchParams();
  const interactions = useInteractions();
  const detailOpen = params.get("view") === "detail";
  const myOpen = params.get("view") === "my";
  const savedOpen = params.get("view") === "saved";
  const activityOpen = params.get("view") === "activity";
  const inquiryOpen = params.get("view") === "inquiry";
  const inboxSource = useMemo(() => homeSource.kind === "demo" ? createDemoNotificationInbox() : notificationSource, [homeSource.kind, notificationSource]);
  const inboxOpen = !!inboxSource && params.get("view") === "notifications";
  const serviceHasNewNotice = useContext(NewNoticeContext);
  const [demoHasNewNotice, setDemoHasNewNotice] = useState(false);
  const hasNewNotice = homeSource.kind === "demo" ? demoHasNewNotice : serviceHasNewNotice;
  useEffect(() => {
    if (inboxSource?.kind !== "demo" || inboxOpen) return; // Demo stays isolated from the shared service session.
    const controller = new AbortController();
    inboxSource.hasNewToday(controller.signal).then(
      (value) => { if (!controller.signal.aborted) setDemoHasNewNotice(value); },
      () => {},   // 확인에 실패해도 화면은 그대로 쓴다
    );
    return () => controller.abort();
  }, [inboxSource, inboxOpen]);
  const unreadOnly = params.get("unread") === "1";
  const noticeParam = params.get("notificationId");
  const parsedNotice = noticeParam && /^[1-9]\d*$/.test(noticeParam) ? Number(noticeParam) : null;
  const noticeId = Number.isSafeInteger(parsedNotice) ? parsedNotice : null;
  const detailFromActivity = params.get("from") === "activity";
  const selectedActivityTab = activityTab(params.get("activityTab"));
  const detailFromSaved = params.get("from") === "saved";
  const inboxFromOtherRoute = inboxOpen && ["profile", "taste", "interests", "debate"].includes(params.get("from") ?? "");
  const postId = params.get("postId") ?? "";
  const contentId = params.get("contentId") ?? "";
  const topicParam = params.get("topicId");
  const parsedTopic = topicParam && /^[1-9]\d*$/.test(topicParam) ? Number(topicParam) : undefined;
  const topicId = Number.isSafeInteger(parsedTopic) ? parsedTopic : undefined;
  const home = useRef<HTMLDivElement>(null);
  // TALK 에 다녀오기 전에 보던 피드 (같은 로그인 · 1시간 안): 다시 받지 않고 그대로 펼치고 보던 자리로 간다
  const [talkReturn] = useState(() => feedOwner ? peekHomeFeed(feedOwner) : null);
  const talkSnapshot = useRef<HomeReturnSnapshot | null>(talkReturn?.snapshot ?? null);
  const awayAtStart = useRef(detailOpen || myOpen || savedOpen || activityOpen || inquiryOpen || inboxOpen);
  useEffect(() => {
    if (!talkReturn) return;
    forgetHomeFeed();
    // 상세 · MY 같은 화면이 열린 채로 돌아왔고(뒤로 가기) 그 화면을 열 때 남긴 피드 자리가 있으면 그 자리를 쓴다.
    // 그 자리에는 그때 누른 카드의 포커스까지 있어서, 화면을 닫으면 처음 그대로 돌아간다.
    if (awayAtStart.current && readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind)) talkSnapshot.current = null;
  }, [talkReturn, homeSource.kind]);
  const feedKeeper = useRef<(() => KeptHomeFeed | null) | null>(null);
  const savedList = useRef<HTMLDivElement>(null);
  const activityList = useRef<HTMLDivElement>(null);
  const activityReturn = useRef<{ scrollY: number; contentId: string } | null>(null);
  const detail = useRef<HTMLDivElement>(null);
  const detailReturn = useRef<{ scrollY: number; focus: HTMLElement | null } | null>(null);
  const restoreFrame = useRef<number>(0);
  const [restoreQuery, setRestoreQuery] = useState<string>();
  const safeSavedSource = savedSource.kind === homeSource.kind ? savedSource : pendingSavedSource;
  const [serviceBookmarks] = useState(() => createServiceBookmarkControls(undefined, interactions?.activity.assertOwner,
    (target, saved) => interactions?.activity.record(target, saved ? "bookmark" : "bookmark_cancel", {
      surface: new URLSearchParams(window.location.search).get("view") === "detail" ? "detail" : "home_feed",
    }), interactions?.activity.authorize));
  useEffect(() => () => serviceBookmarks.cancel(), [serviceBookmarks]);
  const bookmarks = homeSource.kind === "demo" && safeSavedSource.kind === "demo" && demoBookmarks?.kind === "demo" ? demoBookmarks
    : homeSource.kind === "service" ? serviceBookmarks : undefined;
  const connectedSavedSource = useMemo<SavedDataSource>(() => safeSavedSource.kind === "service" ? {
    kind: "service",
    load: (options) => safeSavedSource.load(options),
    subscribe(listener) {
      const original = safeSavedSource.subscribe?.(listener);
      const mutations = serviceBookmarks.subscribeChanges(listener);
      return () => { original?.(); mutations(); };
    },
  } : safeSavedSource, [safeSavedSource, serviceBookmarks]);
  useSyncExternalStore(bookmarks?.subscribe ?? noSubscribe, bookmarks?.getSnapshot ?? noSnapshot, noSnapshot);
  const restoreHome = useCallback(() => {
    if (detailOpen || myOpen || savedOpen || activityOpen || inquiryOpen || inboxOpen) return;
    // TALK 에 다녀왔으면 그때 기억해 둔 자리로 간다 (브라우저 기록에 남은 예전 자리보다 새로워서 먼저 쓴다).
    // 실제로 되돌린 뒤에 지운다: 이 함수가 두 번 불려도(개발 모드 등) 앞의 예약이 취소되고 뒤의 예약이 같은 자리를 쓴다.
    const snapshot = talkSnapshot.current ?? readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind);
    if (snapshot) setRestoreQuery(snapshot.query);
    cancelAnimationFrame(restoreFrame.current);
    restoreFrame.current = requestAnimationFrame(() => {
      if (snapshot && snapshot === talkSnapshot.current) talkSnapshot.current = null;
      if (!snapshot) {
        scrollHomeTo(0);
        home.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true });
        return;
      }
      // Seed every lightweight slot, including cards which are not mounted yet.
      home.current?.querySelectorAll<HTMLElement>("[data-home-feed-window]").forEach(list => {
        list.dispatchEvent(new CustomEvent<HomeTrackRestore>(RESTORE_HOME_TRACKS, {
          detail: { tracks: snapshot.tracks, focusContentId: snapshot.focusContentId },
        }));
      });
      home.current?.querySelectorAll<HTMLElement>("[data-home-post]").forEach((post) => {
        const track = post.querySelector<HTMLElement>("[data-home-track]");
        const saved = snapshot.tracks.find((item) => item.postId === post.dataset.homePost);
        if (track && saved) track.scrollTo({ left: saved.index * track.clientWidth, behavior: "instant" });
      });
      scrollHomeTo(snapshot.scrollY);
      // The restored opener may need one React commit to mount after TALK.
      // The existing frame ref also cancels this focus if navigation changes.
      restoreFrame.current = requestAnimationFrame(() => {
        const button = Array.from(home.current?.querySelectorAll<HTMLButtonElement>("[data-home-detail]") ?? [])
          .find((item) => item.dataset.homeDetail === snapshot.focusContentId);
        const navigation = snapshot.focusNavigation ?? "my";
        (button ?? (snapshot.focusContentId === "" ? home.current?.querySelector<HTMLButtonElement>(`[data-${navigation}-navigation]`) : null)
          ?? home.current?.querySelector<HTMLHeadingElement>("h1"))?.focus({ preventScroll: true });
      });
    });
  }, [detailOpen, myOpen, savedOpen, activityOpen, inquiryOpen, inboxOpen, homeSource.kind]);
  const restoreSaved = useCallback(() => {
    if (!savedOpen) return;
    const snapshot = readSavedReturn(window.history.state?.pivotDetailSaved, window.location.pathname, homeSource.kind);
    cancelAnimationFrame(restoreFrame.current);
    restoreFrame.current = requestAnimationFrame(() => {
      window.scrollTo({ top: snapshot?.scrollY ?? 0, behavior: "instant" });
      const button = Array.from(savedList.current?.querySelectorAll<HTMLButtonElement>("[data-saved-content]") ?? [])
        .find((item) => item.dataset.savedContent === snapshot?.focusContentId);
      (button ?? savedList.current?.querySelector<HTMLHeadingElement>("h1"))?.focus({ preventScroll: true });
    });
  }, [savedOpen, homeSource.kind]);
  useEffect(() => {
    if (detailOpen) {
      const saved = detailReturn.current;
      window.scrollTo({ top: saved?.scrollY ?? 0, behavior: "instant" });
      if (saved) (saved.focus ?? detail.current?.querySelector<HTMLHeadingElement>("h1"))?.focus({ preventScroll: true });
    }
    else if (myOpen || inquiryOpen) window.scrollTo({ top: 0, behavior: "instant" });
    else if (activityOpen) {
      restoreFrame.current = requestAnimationFrame(() => {
        window.scrollTo({ top: activityReturn.current?.scrollY ?? 0, behavior: "instant" });
        const button = Array.from(activityList.current?.querySelectorAll<HTMLButtonElement>("[data-activity-content]") ?? [])
          .find(item => item.dataset.activityContent === activityReturn.current?.contentId);
        (button ?? activityList.current?.querySelector<HTMLHeadingElement>("h1"))?.focus({ preventScroll: true });
      });
    }
    else if (savedOpen) restoreSaved();
    else restoreHome();
    return () => cancelAnimationFrame(restoreFrame.current);
  }, [detailOpen, myOpen, savedOpen, activityOpen, inquiryOpen, postId, contentId, topicId, restoreHome, restoreSaved]);
  function captureHome(focusContentId: string, focusNavigation?: HomeReturnSnapshot["focusNavigation"]): HomeReturnSnapshot {
    return {
      path: window.location.pathname, kind: homeSource.kind, scrollY: homeScrollY(),
      query: home.current?.querySelector<HTMLInputElement>("input[type=search]")?.value ?? "",
      focusContentId, focusNavigation,
      tracks: Array.from(home.current?.querySelectorAll<HTMLElement>("[data-home-feed-slot]") ?? []).map((slot) => {
        const track = slot.querySelector<HTMLElement>("[data-home-track]");
        const cached = Number(slot.dataset.homeSlideIndex ?? 0);
        return { postId: slot.dataset.homeFeedSlot ?? "", index: track?.clientWidth ? Math.round(track.scrollLeft / track.clientWidth)
          : Number.isInteger(cached) && cached >= 0 ? cached : 0 };
      }),
    };
  }
  // TALK 을 누르면(HomeNavigation 이 알림) 지금 피드와 보던 자리를 기억해 둔다. 피드를 보고 있었으면 지금 자리를,
  // 상세 · MY 같은 다른 화면에 있었으면 그 화면을 열 때 남겨 둔 피드 자리를 쓴다. 돌아오면 TALK 버튼에 포커스를 둔다.
  useEffect(() => {
    if (!feedOwner) return;
    const leave = () => {
      // 카드의 저장 · 좋아요 · 선호는 지금 화면에 보이는 값으로 넣어 둔다 (끝나지 않은 요청이 있으면 기억하지 않는다)
      const kept = feedKeeper.current?.();
      const feed = kept ? withShownReactions(kept, { bookmarks: serviceBookmarks, reactions: interactions?.reactions }) : null;
      if (!feed) return;
      const away = detailOpen || myOpen || savedOpen || activityOpen || inquiryOpen || inboxOpen;
      // 다른 화면이 열린 채로 TALK 에서 돌아와 아직 되돌리지 않은 자리가 있으면 그것이 가장 새 자리다
      const place = away ? talkSnapshot.current ?? readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind) : captureHome("");
      const snapshot: HomeReturnSnapshot = place ?? { path: window.location.pathname, kind: homeSource.kind, scrollY: 0, query: "", focusContentId: "", tracks: [] };
      // 검색 결과를 보고 있었으면 그 자리는 검색 결과 목록의 자리라서, 돌아온 피드는 맨 위부터 보여 준다
      const searched = !!home.current?.querySelector("#home-search-results");
      keepHomeFeed(feedOwner, { feed, snapshot: { ...snapshot, ...(searched ? { scrollY: 0, tracks: [] } : {}), focusContentId: "", focusNavigation: "talk" } });
    };
    window.addEventListener(LEAVE_HOME_FOR_TALK, leave);
    return () => window.removeEventListener(LEAVE_HOME_FOR_TALK, leave);
  }, [feedOwner, detailOpen, myOpen, savedOpen, activityOpen, inquiryOpen, inboxOpen, homeSource.kind, interactions, serviceBookmarks]);
  function captureSaved(focusContentId: string): SavedReturnSnapshot {
    return { path: window.location.pathname, kind: homeSource.kind, scrollY: window.scrollY, focusContentId };
  }
  function storedSnapshots() {
    return {
      pivotDetailHome: readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind),
      pivotDetailSaved: readSavedReturn(window.history.state?.pivotDetailSaved, window.location.pathname, homeSource.kind),
    };
  }
  function openDetail(target: HomeDetailTarget) {
    interactions?.activity.remember(target);
    detailReturn.current = null;
    if (activityOpen) {
      activityReturn.current = { scrollY: window.scrollY, contentId: target.contentId };
      window.history.pushState({ ...storedSnapshots(), pivotDetailBack: true }, "", `${detailHref(window.location.pathname, target)}&from=activity&activityTab=${selectedActivityTab}`);
      return;
    }
    if (savedOpen) {
      const history = { ...storedSnapshots(), pivotDetailSaved: captureSaved(target.contentId) };
      window.history.replaceState({ ...history, pivotSavedBack: window.history.state?.pivotSavedBack === true }, "", `${window.location.pathname}?view=saved`);
      window.history.pushState({ ...history, pivotDetailBack: true }, "", `${detailHref(window.location.pathname, target)}&from=saved`);
      return;
    }
    const snapshot = captureHome(target.contentId);
    // Native History integrates with Next search params without remounting Home.
    // History state survives refresh and does not require storage permission.
    // Pass only our state. Next copies its own internal fields; copying those
    // fields ourselves bypasses the router's search-param update hook.
    window.history.replaceState({ pivotDetailHome: snapshot }, "", `${snapshot.path}?view=home`);
    window.history.pushState({ pivotDetailHome: snapshot, pivotDetailBack: true }, "", detailHref(snapshot.path, target));
  }
  function openSection(next: "my" | "saved") {
    const history = storedSnapshots();
    const atHome = !detailOpen && !myOpen && !savedOpen && !activityOpen && !inquiryOpen;
    if (atHome) {
      history.pivotDetailHome = captureHome("", next);
      history.pivotDetailSaved = null;
      // Next adds router fields to the argument. Never reuse that mutated object
      // in pushState, where those fields would bypass search-param updates.
      window.history.replaceState({ ...history }, "", `${window.location.pathname}?view=home`);
    }
    if (savedOpen) {
      history.pivotDetailSaved = captureSaved("");
      window.history.replaceState({ ...history, pivotSavedBack: window.history.state?.pivotSavedBack === true }, "", `${window.location.pathname}${window.location.search}`);
    }
    if (detailOpen) detailReturn.current = { scrollY: window.scrollY, focus: document.activeElement as HTMLElement | null };
    const query = new URLSearchParams({ view: next });
    // Retain Detail identity and origin while navigating between SAVE and MY.
    if ((detailOpen || myOpen || savedOpen || inquiryOpen) && postId && contentId) {
      query.set("postId", postId); query.set("contentId", contentId);
      if (topicId !== undefined) query.set("topicId", String(topicId));
      if (detailFromSaved) query.set("from", "saved");
      if (detailFromActivity) { query.set("from", "activity"); query.set("activityTab", selectedActivityTab); }
    }
    window.history.pushState({ ...history, pivotMyBack: next === "my" && atHome, pivotSavedBack: next === "saved" && atHome }, "", `${window.location.pathname}?${query}`);
  }
  function openMy() { openSection("my"); }
  function openSaved() { if (detailOpen && detailFromSaved) backFromDetail(); else openSection("saved"); }
  function inboxHref(unread: boolean, id?: number) {
    const query = new URLSearchParams({ view: "notifications" });
    if (unread) query.set("unread", "1");
    if (id !== undefined) query.set("notificationId", String(id));
    if (inboxFromOtherRoute) query.set("from", params.get("from")!);
    return `${window.location.pathname}?${query}`;
  }
  function openInbox() {
    if (detailOpen || myOpen || savedOpen || activityOpen || inquiryOpen) {
      // From SAVE, MY, activity or inquiry, back from the inbox pops this entry and returns there.
      window.history.pushState({ ...storedSnapshots(), pivotInboxBack: true }, "", inboxHref(false));
      return;
    }
    const snapshot = captureHome("", "notification");
    window.history.replaceState({ pivotDetailHome: snapshot }, "", `${snapshot.path}${window.location.search}`);
    window.history.pushState({ pivotDetailHome: snapshot, pivotInboxBack: true }, "", inboxHref(false));
  }
  function inboxHistory() {
    return { ...storedSnapshots(), pivotInboxBack: window.history.state?.pivotInboxBack === true };
  }
  function selectInboxFilter(unread: boolean) {
    if (unread !== unreadOnly) window.history.replaceState(inboxHistory(), "", inboxHref(unread));
  }
  function openNotice(id: number) {
    window.history.pushState({ ...inboxHistory(), pivotNoticeBack: true }, "", inboxHref(unreadOnly, id));
  }
  function openInboxContent(item: InboxNotification) {
    const target = newPostTarget(item);
    if (!target) return;
    detailReturn.current = null;
    // Back from the post pops this entry and returns to the inbox, which keeps the Home snapshot.
    window.history.pushState({ ...inboxHistory(), pivotDetailBack: true, pivotDetailFromInbox: true }, "", detailHref(window.location.pathname, target));
  }
  // 다른 화면(프로필 · 취향 · 관심 · 토론방)에서 온 알림함은 그 화면으로 돌아간다.
  // 단 기록이 이 한 장뿐인 탭(주소를 바로 연 경우)은 돌아갈 곳이 없어서 예전처럼 HOME 으로 간다.
  function backFromInbox() {
    if (noticeId !== null) {
      if (window.history.state?.pivotNoticeBack) window.history.back();
      else window.history.replaceState(inboxHistory(), "", inboxHref(unreadOnly));
    } else if (window.history.state?.pivotInboxBack || (inboxFromOtherRoute && window.history.length > 1)) window.history.back();
    else backToHome();
  }
  function openActivity() {
    activityReturn.current = null;
    window.history.pushState({ ...storedSnapshots(), pivotActivityBack: true }, "", `${window.location.pathname}?view=activity&activityTab=likes`);
  }
  function openInquiry() {
    const query = new URLSearchParams(params.toString());
    query.set("view", "inquiry");
    window.history.pushState({ ...storedSnapshots(), pivotInquiryBack: true }, "", `${window.location.pathname}?${query}`);
  }
  function selectActivityTab(tab: ActivityTab) {
    if (tab === selectedActivityTab) return;
    activityReturn.current = null;
    window.history.replaceState({ ...storedSnapshots(), pivotActivityBack: window.history.state?.pivotActivityBack === true }, "", `${window.location.pathname}?view=activity&activityTab=${tab}`);
  }
  function backToMy() {
    if (window.history.state?.pivotActivityBack || window.history.state?.pivotInquiryBack) window.history.back();
    else window.history.replaceState(storedSnapshots(), "", `${window.location.pathname}?view=my`);
  }
  function backToHome() {
    detailReturn.current = null;
    const snapshot = readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind);
    if (!activityOpen && !inquiryOpen && !detailFromActivity && !window.history.state?.pivotDetailFromInbox && (myOpen ? window.history.state?.pivotMyBack : savedOpen ? window.history.state?.pivotSavedBack : !detailFromSaved && window.history.state?.pivotDetailBack) && snapshot) window.history.back();
    else window.history.replaceState({ pivotDetailHome: snapshot, pivotDetailBack: false }, "", `${window.location.pathname}?view=home`);
  }
  function backFromDetail() {
    if (window.history.state?.pivotDetailFromInbox) {
      detailReturn.current = null;
      window.history.back();
      return;
    }
    if (detailFromActivity) {
      detailReturn.current = null;
      if (window.history.state?.pivotDetailBack) window.history.back();
      else window.history.replaceState(storedSnapshots(), "", `${window.location.pathname}?view=activity&activityTab=${selectedActivityTab}`);
      return;
    }
    if (!detailFromSaved) { backToHome(); return; }
    detailReturn.current = null;
    const history = storedSnapshots();
    if (window.history.state?.pivotDetailBack && history.pivotDetailSaved) window.history.back();
    else window.history.replaceState(history, "", `${window.location.pathname}?view=saved`);
  }
  return <NewNoticeContext.Provider value={hasNewNotice}><InteractionViewContext.Provider value={detailOpen ? "detail" : myOpen ? "my" : savedOpen ? "saved" : activityOpen ? "activity" : inquiryOpen ? "inquiry" : inboxOpen ? "notifications" : "home"}>
    <div ref={home} hidden={detailOpen || myOpen || savedOpen || activityOpen || inquiryOpen || inboxOpen}>
      <HomeController source={homeSource} skipEntryLoading={skipEntryLoading || !!talkReturn} resume={talkReturn?.feed} keeper={feedKeeper} onRestart={onRestart} onViewDetail={openDetail} onReady={restoreHome} restoreQuery={restoreQuery} onMy={openMy} onSaved={openSaved} onNotifications={inboxSource ? openInbox : undefined} bookmarks={bookmarks} />
    </div>
    {inboxOpen && <NotificationInbox key={String(unreadOnly)} source={inboxSource} unreadOnly={unreadOnly} id={noticeId}
      onFilter={selectInboxFilter} onOpen={openNotice} onOpenContent={openInboxContent} onBack={backFromInbox} />}
    <div ref={detail} hidden={!detailOpen}>
      {detailOpen || ((myOpen || savedOpen || inquiryOpen) && postId && contentId) ? <DetailController source={detailSource} reportSource={homeSource.kind === "service" ? reportSource : undefined} target={{ postId, contentId, ...(topicId === undefined ? {} : { topicId }), ...interactions?.activity.metadata({ postId, contentId, topicId }) }} onBack={backFromDetail} onHome={backToHome} onMy={openMy} onSaved={openSaved} fromSaved={detailFromSaved} fromActivity={detailFromActivity} bookmarks={bookmarks} /> : null}
    </div>
    <div ref={savedList} hidden={!savedOpen}>
      {savedOpen || (detailFromSaved && (detailOpen || myOpen || inquiryOpen)) ? <SavedController source={connectedSavedSource} onHome={backToHome} onMy={openMy} onViewDetail={openDetail} onReady={restoreSaved} onNotifications={inboxSource ? openInbox : undefined} /> : null}
    </div>
    <div ref={activityList} hidden={!activityOpen}>
      {activityOpen || (detailFromActivity && (detailOpen || myOpen || savedOpen || inquiryOpen)) ? <MyActivityController source={activitySource} demo={homeSource.kind === "demo"} tab={selectedActivityTab} onTab={selectActivityTab} onHome={backToHome} onMy={backToMy} onSaved={openSaved} onViewDetail={openDetail} onNotifications={inboxSource ? openInbox : undefined} /> : null}
    </div>
    {myOpen ? <MyPageController source={mySource.kind === homeSource.kind ? mySource : pendingMyPageSource} onHome={backToHome} onSaved={openSaved} onActivity={openActivity} onInquiry={openInquiry} onLogout={homeSource.kind === "service" ? onLogout : undefined} onWithdraw={homeSource.kind === "service" ? onWithdraw : undefined} onNotifications={inboxSource ? openInbox : undefined} /> : null}
    {inquiryOpen ? <InquiryController source={homeSource.kind === "service" ? inquirySource : undefined} demo={homeSource.kind === "demo"} onMy={backToMy} onHome={backToHome} onSaved={openSaved} onNotifications={inboxSource ? openInbox : undefined} /> : null}
  </InteractionViewContext.Provider></NewNoticeContext.Provider>;
}
