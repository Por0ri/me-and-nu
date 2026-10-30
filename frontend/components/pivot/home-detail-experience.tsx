"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import type { HomeDataSource, HomeDetailTarget } from "@/lib/pivot/home";
import type { DetailDataSource } from "@/lib/pivot/detail";
import { detailHref, readHomeReturn, type HomeReturnSnapshot } from "@/lib/pivot/detail-navigation";
import type { MyPageDataSource } from "@/lib/pivot/my-page";
import { pendingMyPageSource } from "@/lib/pivot/my-page-service";
import type { DemoBookmarkControls, SavedDataSource } from "@/lib/pivot/saved";
import { pendingSavedSource } from "@/lib/pivot/saved-service";
import { createServiceBookmarkControls } from "@/lib/pivot/bookmark-service";
import { readSavedReturn, type SavedReturnSnapshot } from "@/lib/pivot/saved-navigation";
import { HomeController } from "./home-controller";
import { DetailController } from "./detail-controller";
import { MyPageController } from "./my-page-controller";
import { SavedController } from "./saved-controller";
import { InteractionViewContext, useInteractions } from "./interaction-context";

const noSubscribe = () => () => {};
const noSnapshot = () => 0;

export function HomeDetailExperience({ homeSource, detailSource, mySource = pendingMyPageSource, savedSource = pendingSavedSource, demoBookmarks, onRestart, onLogout }: {
  homeSource: HomeDataSource; detailSource: DetailDataSource; mySource?: MyPageDataSource; savedSource?: SavedDataSource;
  demoBookmarks?: DemoBookmarkControls; onRestart?: () => void; onLogout?: () => Promise<void>;
}) {
  const params = useSearchParams();
  const interactions = useInteractions();
  const detailOpen = params.get("view") === "detail";
  const myOpen = params.get("view") === "my";
  const savedOpen = params.get("view") === "saved";
  const detailFromSaved = params.get("from") === "saved";
  const postId = params.get("postId") ?? "";
  const contentId = params.get("contentId") ?? "";
  const topicParam = params.get("topicId");
  const parsedTopic = topicParam && /^[1-9]\d*$/.test(topicParam) ? Number(topicParam) : undefined;
  const topicId = Number.isSafeInteger(parsedTopic) ? parsedTopic : undefined;
  const home = useRef<HTMLDivElement>(null);
  const savedList = useRef<HTMLDivElement>(null);
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
    if (detailOpen || myOpen || savedOpen) return;
    const snapshot = readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind);
    if (snapshot) setRestoreQuery(snapshot.query);
    cancelAnimationFrame(restoreFrame.current);
    restoreFrame.current = requestAnimationFrame(() => {
      if (!snapshot) {
        window.scrollTo({ top: 0, behavior: "instant" });
        home.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true });
        return;
      }
      home.current?.querySelectorAll<HTMLElement>("[data-home-post]").forEach((post) => {
        const track = post.querySelector<HTMLElement>("[data-home-track]");
        const saved = snapshot.tracks.find((item) => item.postId === post.dataset.homePost);
        if (track && saved) track.scrollTo({ left: saved.index * track.clientWidth, behavior: "instant" });
      });
      window.scrollTo({ top: snapshot.scrollY, behavior: "instant" });
      const button = Array.from(home.current?.querySelectorAll<HTMLButtonElement>("[data-home-detail]") ?? [])
        .find((item) => item.dataset.homeDetail === snapshot.focusContentId);
      (button ?? (snapshot.focusContentId === "" ? home.current?.querySelector<HTMLButtonElement>(snapshot.focusNavigation === "saved" ? "[data-saved-navigation]" : "[data-my-navigation]") : null)
        ?? home.current?.querySelector<HTMLHeadingElement>("h1"))?.focus({ preventScroll: true });
    });
  }, [detailOpen, myOpen, savedOpen, homeSource.kind]);
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
    else if (myOpen) window.scrollTo({ top: 0, behavior: "instant" });
    else if (savedOpen) restoreSaved();
    else restoreHome();
    return () => cancelAnimationFrame(restoreFrame.current);
  }, [detailOpen, myOpen, savedOpen, postId, contentId, topicId, restoreHome, restoreSaved]);
  function captureHome(focusContentId: string, focusNavigation?: "my" | "saved"): HomeReturnSnapshot {
    return {
      path: window.location.pathname, kind: homeSource.kind, scrollY: window.scrollY,
      query: home.current?.querySelector<HTMLInputElement>("input[type=search]")?.value ?? "",
      focusContentId, focusNavigation,
      tracks: Array.from(home.current?.querySelectorAll<HTMLElement>("[data-home-post]") ?? []).map((post) => {
        const track = post.querySelector<HTMLElement>("[data-home-track]");
        return { postId: post.dataset.homePost ?? "", index: track?.clientWidth ? Math.round(track.scrollLeft / track.clientWidth) : 0 };
      }),
    };
  }
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
    const atHome = !detailOpen && !myOpen && !savedOpen;
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
    if ((detailOpen || myOpen || savedOpen) && postId && contentId) {
      query.set("postId", postId); query.set("contentId", contentId);
      if (topicId !== undefined) query.set("topicId", String(topicId));
      if (detailFromSaved) query.set("from", "saved");
    }
    window.history.pushState({ ...history, pivotMyBack: next === "my" && atHome, pivotSavedBack: next === "saved" && atHome }, "", `${window.location.pathname}?${query}`);
  }
  function openMy() { openSection("my"); }
  function openSaved() { if (detailOpen && detailFromSaved) backFromDetail(); else openSection("saved"); }
  function backToHome() {
    detailReturn.current = null;
    const snapshot = readHomeReturn(window.history.state?.pivotDetailHome, window.location.pathname, homeSource.kind);
    if ((myOpen ? window.history.state?.pivotMyBack : savedOpen ? window.history.state?.pivotSavedBack : !detailFromSaved && window.history.state?.pivotDetailBack) && snapshot) window.history.back();
    else window.history.replaceState({ pivotDetailHome: snapshot, pivotDetailBack: false }, "", `${window.location.pathname}?view=home`);
  }
  function backFromDetail() {
    if (!detailFromSaved) { backToHome(); return; }
    detailReturn.current = null;
    const history = storedSnapshots();
    if (window.history.state?.pivotDetailBack && history.pivotDetailSaved) window.history.back();
    else window.history.replaceState(history, "", `${window.location.pathname}?view=saved`);
  }
  return <InteractionViewContext.Provider value={detailOpen ? "detail" : myOpen ? "my" : savedOpen ? "saved" : "home"}>
    <div ref={home} hidden={detailOpen || myOpen || savedOpen}>
      <HomeController source={homeSource} onRestart={onRestart} onViewDetail={openDetail} onReady={restoreHome} restoreQuery={restoreQuery} onMy={openMy} onSaved={openSaved} bookmarks={bookmarks} />
    </div>
    <div ref={detail} hidden={!detailOpen}>
      {detailOpen || ((myOpen || savedOpen) && postId && contentId) ? <DetailController source={detailSource} target={{ postId, contentId, ...(topicId === undefined ? {} : { topicId }), ...interactions?.activity.metadata({ postId, contentId, topicId }) }} onBack={backFromDetail} onHome={backToHome} onMy={openMy} onSaved={openSaved} fromSaved={detailFromSaved} bookmarks={bookmarks} /> : null}
    </div>
    <div ref={savedList} hidden={!savedOpen}>
      {savedOpen || (detailFromSaved && (detailOpen || myOpen)) ? <SavedController source={connectedSavedSource} onHome={backToHome} onMy={openMy} onViewDetail={openDetail} onReady={restoreSaved} /> : null}
    </div>
    {myOpen ? <MyPageController source={mySource.kind === homeSource.kind ? mySource : pendingMyPageSource} onHome={backToHome} onSaved={openSaved} onLogout={homeSource.kind === "service" ? onLogout : undefined} /> : null}
  </InteractionViewContext.Provider>;
}
