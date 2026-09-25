"use client";

import { useState } from "react";
import {
  ApiError, addMyTopic, apiBaseUrl, bookmarkContent, clearPreference,
  getContent, getFeed, getMySubtopics, getMyTopics, getPolicies, getProfile,
  getSession, getSubtopics, getTopics, likeContent, login, logout, onboard,
  register, setPreference, subscribeSubtopic, unbookmarkContent, unlikeContent,
  unsubscribeSubtopic, updateProfile,
  type ContentDetail, type FeedResponse, type MyTopicsResponse, type Policy,
  type PolicyType, type Profile, type Session, type Subtopic, type Topic,
} from "@/lib/api";

const field = "w-full rounded-xl border border-white/10 bg-slate-950/60 px-3 py-2.5 text-sm outline-none focus:border-violet-300/60";
const button = "rounded-xl border border-white/15 px-3 py-2 text-sm font-semibold hover:border-teal-300/60 hover:bg-teal-300/10 disabled:cursor-not-allowed disabled:opacity-45";

type Result = { title: string; data?: unknown; error?: string; status?: number; code?: string };

/** Keep the CSRF token in api.ts memory. It must not be printed in the debug panel. */
function safeResult(data: unknown): unknown {
  if (data && typeof data === "object" && "csrfToken" in data) {
    const { csrfToken: _secret, ...rest } = data as Session;
    return { ...rest, csrfReady: Boolean(_secret) };
  }
  return data;
}

function policyType(policy: Policy): PolicyType {
  return policy.type;
}

export default function AuthIntegrationTest() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [role, setRole] = useState<"consumer" | "creator">("consumer");
  const [birthDate, setBirthDate] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [consents, setConsents] = useState<Record<string, boolean>>({ terms: true, privacy: true });
  const [topics, setTopics] = useState<Topic[]>([]);
  const [topicId, setTopicId] = useState<number | null>(null);
  const [subtopics, setSubtopics] = useState<Subtopic[]>([]);
  const [selectedSubtopicIds, setSelectedSubtopicIds] = useState<number[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [myTopics, setMyTopics] = useState<MyTopicsResponse | null>(null);
  const [mySubtopicIds, setMySubtopicIds] = useState<number[]>([]);
  const [feed, setFeed] = useState<FeedResponse | null>(null);
  const [feedCursor, setFeedCursor] = useState<string | null>(null);
  const [feedSubtopicId, setFeedSubtopicId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ContentDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  async function run<T>(title: string, action: () => Promise<T>, after?: (value: T) => void) {
    setBusy(title);
    try {
      const data = await action();
      after?.(data);
      setResult({ title, data: safeResult(data) ?? "204 No Content" });
    } catch (error) {
      setResult({
        title,
        error: error instanceof Error ? error.message : "알 수 없는 오류",
        status: error instanceof ApiError ? error.status : undefined,
        code: error instanceof ApiError ? error.code : undefined,
      });
    } finally {
      setBusy(null);
    }
  }

  function refreshSession() {
    void run("세션 조회", getSession, setSession);
  }

  function loadCatalog() {
    void run("정책·Topic 조회", async () => {
      const [policyResult, topicResult] = await Promise.all([getPolicies(), getTopics()]);
      setPolicies(policyResult.consentItems);
      setTopics(topicResult.topics);
      const nextTopicId = topicId ?? topicResult.topics[0]?.id ?? null;
      setTopicId(nextTopicId);
      if (nextTopicId !== null) {
        const subtopicResult = await getSubtopics(nextTopicId);
        setSubtopics(subtopicResult.items);
      }
      return { policies: policyResult.consentItems, topics: topicResult.topics };
    });
  }

  function loadSubtopics(nextTopicId: number) {
    setTopicId(nextTopicId);
    setSelectedSubtopicIds([]);
    setFeed(null);
    setDetail(null);
    void run("Subtopic 조회", () => getSubtopics(nextTopicId), (data) => setSubtopics(data.items));
  }

  function submitOnboarding() {
    const input = {
      accountType: role,
      birthDate,
      nickname,
      profileImageId: null,
      consents: policies.map((policy) => ({
        type: policyType(policy),
        policyVersion: policy.policyVersion,
        agreed: Boolean(consents[policy.type]),
      })),
      ...(role === "consumer" && topicId !== null
        ? { topicId, subtopicIds: selectedSubtopicIds }
        : {}),
    };
    void run("온보딩", async () => {
      const response = await onboard(input);
      setSession(await getSession());
      setProfile(await getProfile());
      setMyTopics(await getMyTopics());
      if (role === "consumer" && topicId !== null) {
        const own = await getMySubtopics(topicId);
        setMySubtopicIds(own.items.map((item) => item.subtopicId));
      }
      return response;
    });
  }

  function loadPersonal() {
    void run("내 프로필·관심사 조회", async () => {
      const [nextProfile, nextTopics] = await Promise.all([getProfile(), getMyTopics()]);
      setProfile(nextProfile);
      setNickname(nextProfile.nickname);
      setMyTopics(nextTopics);
      if (topicId !== null) {
        const own = await getMySubtopics(topicId);
        setMySubtopicIds(own.items.map((item) => item.subtopicId));
      }
      return { profile: nextProfile, myTopics: nextTopics };
    });
  }

  function loadFeed(cursor?: string) {
    if (topicId === null) return;
    void run("피드 조회", () => getFeed(topicId, {
      subtopicId: feedSubtopicId ?? undefined,
      cursor,
    }), (data) => {
      setFeed(data);
      setFeedCursor(data.nextCursor);
      setDetail(null);
    });
  }

  function loadDetail(contentId: number) {
    if (topicId === null) return;
    void run("콘텐츠 상세", () => getContent(contentId, topicId), setDetail);
  }

  function changeReaction(title: string, action: (contentId: number, selectedTopicId: number) => Promise<unknown>) {
    if (!detail || topicId === null) return;
    const contentId = detail.contentId;
    void run(title, async () => {
      await action(contentId, topicId);
      const [nextDetail, nextFeed] = await Promise.all([
        getContent(contentId, topicId),
        getFeed(topicId, { subtopicId: feedSubtopicId ?? undefined }),
      ]);
      setDetail(nextDetail);
      setFeed(nextFeed);
      setFeedCursor(nextFeed.nextCursor);
      return nextDetail;
    });
  }

  const disabled = busy !== null;
  const cards = feed?.sections.flatMap((section) => section.contents) ?? [];

  return (
    <section className="mt-6 space-y-5 rounded-3xl border border-white/10 bg-white/[0.045] p-6 shadow-2xl shadow-black/10">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-violet-300">V1 로컬 통합 테스트</p>
          <h2 className="mt-1 text-2xl font-bold">가입부터 북마크까지</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            실제 DB와 API로 단계별 확인합니다. 세션 쿠키는 브라우저가 관리하고 CSRF 토큰은 메모리에만 보관합니다.
            정책·Topic·콘텐츠 ID는 조회 결과에서 선택하세요.
          </p>
        </div>
        <span className="break-all rounded-lg bg-slate-950/60 px-3 py-2 font-mono text-xs text-slate-400">{apiBaseUrl}</span>
      </header>

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="rounded-2xl border border-white/10 bg-slate-950/40 p-4">
          <h3 className="font-bold">1. 개발용 가입·로그인·세션</h3>
          <p className="mt-1 text-xs text-slate-400">첫 변경 요청 전에 익명 세션과 CSRF를 자동으로 받습니다.</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-slate-300">이메일<input className={field} type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label className="text-xs text-slate-300">비밀번호<input className={field} type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <label className="text-xs text-slate-300">닉네임<input className={field} value={nickname} onChange={(event) => setNickname(event.target.value)} /></label>
            <label className="text-xs text-slate-300">계정 유형
              <select className={field} value={role} onChange={(event) => setRole(event.target.value as "consumer" | "creator")}>
                <option value="consumer">consumer</option><option value="creator">creator</option>
              </select>
            </label>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={button} disabled={disabled} onClick={() => void run("회원가입", () => register({ email, password, nickname, role }))}>회원가입</button>
            <button className={button} disabled={disabled} onClick={() => void run("로그인", async () => {
              const response = await login({ email, password });
              if (response.user.accountType === "consumer" || response.user.accountType === "creator") {
                setRole(response.user.accountType);
              }
              setSession(await getSession());
              return response;
            })}>로그인</button>
            <button className={button} disabled={disabled} onClick={refreshSession}>세션 조회</button>
            <button className={button} disabled={disabled} onClick={() => void run("로그아웃", async () => {
              await logout();
              setProfile(null); setMyTopics(null); setFeed(null); setDetail(null);
              setSession(await getSession());
            })}>로그아웃</button>
          </div>
          <p className="mt-3 text-xs text-slate-400">
            상태: {session ? `${session.sessionState} · ${session.authenticated ? "인증됨" : "비로그인"}` : "조회 전"}
            {session?.csrfToken ? " · CSRF 준비됨" : ""}
          </p>
        </section>

        <section className="rounded-2xl border border-white/10 bg-slate-950/40 p-4">
          <h3 className="font-bold">2. 정책·Topic·Subtopic</h3>
          <button className={`${button} mt-3`} disabled={disabled} onClick={loadCatalog}>정책·Topic 조회</button>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-slate-300">Topic
              <select className={field} value={topicId ?? ""} onChange={(event) => loadSubtopics(Number(event.target.value))}>
                <option value="" disabled>먼저 Topic 조회</option>
                {topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name} (ID {topic.id})</option>)}
              </select>
            </label>
            <label className="text-xs text-slate-300">생년월일
              <input className={field} type="date" value={birthDate} onChange={(event) => setBirthDate(event.target.value)} />
            </label>
          </div>
          <div className="mt-3 space-y-1">
            {policies.map((policy) => (
              <label key={policy.type} className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={Boolean(consents[policy.type])} onChange={(event) => setConsents({ ...consents, [policy.type]: event.target.checked })} />
                {policy.type} · {policy.policyVersion} {policy.required ? "(필수)" : "(선택)"} · {policy.text}
              </label>
            ))}
          </div>
          <div className="mt-3">
            <p className="text-xs text-slate-400">Subtopic 선택 (consumer 온보딩에는 1개 이상)</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {subtopics.map((subtopic) => (
                <label key={subtopic.subtopicId} className="rounded-lg border border-white/10 px-2 py-1 text-sm">
                  <input className="mr-2" type="checkbox" checked={selectedSubtopicIds.includes(subtopic.subtopicId)}
                    onChange={(event) => setSelectedSubtopicIds(event.target.checked
                      ? [...selectedSubtopicIds, subtopic.subtopicId]
                      : selectedSubtopicIds.filter((id) => id !== subtopic.subtopicId))} />
                  {subtopic.name} ({subtopic.subtopicId})
                </label>
              ))}
            </div>
          </div>
          <button className={`${button} mt-3`} disabled={disabled || !session?.authenticated || policies.length === 0} onClick={submitOnboarding}>온보딩 제출</button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-slate-950/40 p-4">
          <h3 className="font-bold">3. 내 프로필·관심사</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className={button} disabled={disabled} onClick={loadPersonal}>내 정보 조회</button>
            <button className={button} disabled={disabled || !nickname.trim()} onClick={() => void run("닉네임 수정", () => updateProfile({ nickname }), setProfile)}>닉네임 수정</button>
            <button className={button} disabled={disabled || topicId === null} onClick={() => {
              if (topicId === null) return;
              void run("내 Topic 추가", async () => {
                const added = await addMyTopic(topicId, selectedSubtopicIds);
                setMyTopics(await getMyTopics());
                return added;
              });
            }}>선택 Topic 추가</button>
          </div>
          <p className="mt-3 text-xs text-slate-400">프로필: {profile ? `${profile.nickname} · ${profile.accountType}` : "조회 전"}</p>
          <p className="mt-1 text-xs text-slate-400">활성 Topic: {myTopics?.topics.map((topic) => topic.name).join(", ") || "조회 전"}</p>
          {topicId !== null && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button className={button} disabled={disabled} onClick={() => void run("내 Subtopic 조회", () => getMySubtopics(topicId), (data) => setMySubtopicIds(data.items.map((item) => item.subtopicId)))}>구독 조회</button>
              {subtopics.map((subtopic) => (
                <button key={subtopic.subtopicId} className={button} disabled={disabled} onClick={() => void run(
                  mySubtopicIds.includes(subtopic.subtopicId) ? "구독 해제" : "Subtopic 구독",
                  async () => {
                    if (mySubtopicIds.includes(subtopic.subtopicId)) await unsubscribeSubtopic(topicId, subtopic.subtopicId);
                    else await subscribeSubtopic(topicId, subtopic.subtopicId);
                    const own = await getMySubtopics(topicId);
                    setMySubtopicIds(own.items.map((item) => item.subtopicId));
                    return own;
                  },
                )}>{mySubtopicIds.includes(subtopic.subtopicId) ? "해제" : "구독"} · {subtopic.name}</button>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-white/10 bg-slate-950/40 p-4">
          <h3 className="font-bold">4. 피드·콘텐츠 상세</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            <select className={`${field} max-w-52`} value={feedSubtopicId ?? ""} onChange={(event) => setFeedSubtopicId(event.target.value ? Number(event.target.value) : null)}>
              <option value="">모든 Subtopic</option>
              {subtopics.map((subtopic) => <option key={subtopic.subtopicId} value={subtopic.subtopicId}>{subtopic.name}</option>)}
            </select>
            <button className={button} disabled={disabled || topicId === null} onClick={() => loadFeed()}>피드 조회</button>
            {feedCursor && <button className={button} disabled={disabled} onClick={() => loadFeed(feedCursor)}>다음 페이지</button>}
          </div>
          {feed && cards.length === 0 && <p className="mt-3 text-sm text-slate-400">표시할 콘텐츠가 없습니다. {feed.emptyReason}</p>}
          <div className="mt-3 max-h-60 space-y-2 overflow-y-auto">
            {cards.map((card) => (
              <button key={card.id} className="block w-full rounded-xl border border-white/10 p-3 text-left hover:bg-white/5" disabled={disabled} onClick={() => loadDetail(card.id)}>
                <span className="text-xs text-slate-400">ID {card.id} · {card.productionType === "ai" ? "AI 생성 글" : card.productionType === "hybrid" ? "AI 협업 글" : "일반 콘텐츠"} · {card.sourceName ?? "출처 없음"} · {card.saved ? "북마크됨" : "미저장"}</span>
                <strong className="mt-1 block text-sm">{card.title}</strong>
                {card.summary && <span className="mt-1 block text-sm text-slate-300">{card.summary}</span>}
              </button>
            ))}
          </div>
          {detail && <div className="mt-3 rounded-xl border border-teal-300/20 bg-teal-300/5 p-3">
            <p className="text-xs text-teal-300">콘텐츠 ID {detail.contentId}</p>
            <h4 className="mt-1 font-bold">{detail.title}</h4>
            <p className="mt-1 text-xs text-slate-400">{detail.productionType === "ai" ? "AI 생성 글" : detail.productionType === "hybrid" ? "AI 협업 글" : "일반 콘텐츠"}</p>
            {detail.displayMode !== "full_body" && detail.excerpt && <p className="mt-2 text-sm text-slate-300">{detail.excerpt}</p>}
            {detail.displayMode === "full_body" && detail.body && (
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-200">{detail.body}</p>
            )}
            {!detail.excerpt && !detail.body && <p className="mt-2 text-sm text-slate-300">본문 없음</p>}
            <p className="mt-2 text-xs text-slate-400">O/X: {detail.myReaction.preference ?? "없음"} · 좋아요: {detail.myReaction.liked ? "예" : "아니요"} · 북마크: {detail.myReaction.bookmarked ? "예" : "아니요"}</p>
          </div>}
        </section>
      </div>

      <section className="rounded-2xl border border-white/10 bg-slate-950/40 p-4">
        <h3 className="font-bold">5. 선택 콘텐츠의 O/X·좋아요·북마크</h3>
        <p className="mt-1 text-xs text-slate-400">설정·취소 뒤 피드와 상세를 다시 읽어 사용자별 상태를 확인합니다.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("O 설정", (id, topic) => setPreference(id, topic, "positive"))}>O 설정</button>
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("X 설정", (id, topic) => setPreference(id, topic, "negative"))}>X 설정</button>
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("O/X 취소", clearPreference)}>O/X 취소</button>
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("좋아요", likeContent)}>좋아요</button>
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("좋아요 취소", unlikeContent)}>좋아요 취소</button>
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("북마크", bookmarkContent)}>북마크</button>
          <button className={button} disabled={disabled || !detail} onClick={() => changeReaction("북마크 취소", unbookmarkContent)}>북마크 취소</button>
        </div>
      </section>

      <section className="rounded-2xl border border-white/10 bg-slate-950/50 p-4">
        <h3 className="font-bold">마지막 응답</h3>
        {busy && <p className="mt-2 text-sm text-amber-300">{busy} 요청 중…</p>}
        {result?.error && <p className="mt-2 text-sm text-rose-300">{result.title} · HTTP {result.status ?? "?"} · {result.code ?? "ERROR"} · {result.error}</p>}
        {result && !result.error && <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-emerald-200">{result.title} 성공{"\n"}{JSON.stringify(result.data, null, 2)}</pre>}
        {!result && <p className="mt-2 text-sm text-slate-400">세션 조회 또는 회원가입으로 시작하세요.</p>}
      </section>
    </section>
  );
}
