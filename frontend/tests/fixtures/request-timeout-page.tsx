"use client";

import { useEffect, useState } from "react";
import { DetailController } from "@/components/pivot/detail-controller";
import { SavedController } from "@/components/pivot/saved-controller";
import { MyPageController } from "@/components/pivot/my-page-controller";
import { LoginScreen } from "@/components/pivot/login-screen";
import { PivotSessionProvider } from "@/components/pivot/pivot-session";
import type { DetailDataSource } from "@/lib/pivot/detail";
import type { SavedDataSource } from "@/lib/pivot/saved";
import type { MyPageDataSource } from "@/lib/pivot/my-page";
import { fixtureRead, timeoutFixture } from "./request-timeout-transport";
import styles from "@/components/pivot/pivot.module.css";

const target = { postId: "41", contentId: "41", topicId: 7 };
const noop = () => {};
const detail: DetailDataSource = { kind: "service", async load(_, { signal }) {
  await fixtureRead(signal, "detail");
  return { status: "ready", article: { ...target, origin: "service", title: "격리 검증 콘텐츠", sections: [{ paragraphs: ["운영 데이터 없는 로컬 검증 본문입니다."] }] } };
} };
const saved: SavedDataSource = { kind: "service", async load({ signal }) {
  await fixtureRead(signal, "saved");
  return { status: "ready", saved: { origin: "service", cards: [{ ...target, title: "격리 검증 저장 카드" }] } };
} };
const my: MyPageDataSource = { kind: "service", async load({ signal }) {
  await fixtureRead(signal, "my");
  return { status: "ready", profile: { origin: "service", nickname: "격리 검증", maskedEmail: "f***@example.com" } };
} };

export function FixtureRequestTimeoutPage() {
  const [screen, setScreen] = useState("login");
  const [version, setVersion] = useState(0);
  const [events, setEvents] = useState("");
  useEffect(() => { const timer = setInterval(() => setEvents(timeoutFixture.events.join(" · ")), 250); return () => clearInterval(timer); }, []);
  return <div className={styles.root}>
    {screen === "login" ? <PivotSessionProvider key={version}><LoginScreen /></PivotSessionProvider>
      : screen === "detail" ? <DetailController key={version} source={detail} target={target} onBack={noop} />
        : screen === "saved" ? <SavedController key={version} source={saved} onHome={noop} onMy={noop} onViewDetail={noop} onReady={noop} />
          : <MyPageController key={version} source={my} onHome={noop} />}
    <details style={{ position: "fixed", right: 0, top: 0, zIndex: 100, background: "white", maxWidth: 180, fontSize: 12 }}>
      <summary>격리 검증 제어</summary>
      <label>화면<select aria-label="검증 화면" value={screen} onChange={e => setScreen(e.target.value)}>
        <option value="login">로그인</option><option value="detail">DETAIL</option><option value="saved">SAVE</option><option value="my">MY</option>
      </select></label>
      <label>응답<select aria-label="응답 모드" defaultValue="pending" onChange={e => { timeoutFixture.mode = e.target.value as typeof timeoutFixture.mode; }}>
        <option value="pending">영구 pending</option><option value="ready">정상</option><option value="error">네트워크 오류</option>
      </select></label>
      <button onClick={() => setVersion(v => v + 1)}>화면 다시 열기</button>
      <output data-testid="timeout-fixture-events" style={{ display: "block" }}>{events}</output>
    </details>
  </div>;
}
