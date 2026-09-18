"use client";

import { useState } from "react";

import {
  ApiError,
  apiBaseUrl,
  getCurrentUser,
  login,
  logout,
  register,
  type User,
} from "@/lib/api";

type ResultState =
  | { kind: "idle" }
  | { kind: "success"; title: string; data: unknown }
  | { kind: "error"; title: string; message: string; status?: number };

export default function AuthIntegrationTest() {
  const [email, setEmail] = useState("integration02@example.com");
  const [password, setPassword] = useState("test1234");
  const [nickname, setNickname] = useState("프론트테스터");
  const [role, setRole] = useState<"consumer" | "creator">("consumer");
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [result, setResult] = useState<ResultState>({ kind: "idle" });

  async function execute<T>(
    title: string,
    action: () => Promise<T>,
    onSuccess?: (data: T) => void,
  ) {
    setBusyAction(title);

    try {
      const data = await action();
      onSuccess?.(data);
      setResult({ kind: "success", title, data });
    } catch (error) {
      setResult({
        kind: "error",
        title,
        message: error instanceof Error ? error.message : "알 수 없는 오류",
        status: error instanceof ApiError ? error.status : undefined,
      });
    } finally {
      setBusyAction(null);
    }
  }

  function handleRegister() {
    void execute("회원가입", () =>
      register({ email, password, nickname, role }),
    );
  }

  function handleLogin() {
    void execute(
      "로그인",
      () => login({ email, password }),
      (data) => setCurrentUser(data.user),
    );
  }

  function handleMe() {
    void execute("현재 사용자 조회", getCurrentUser, setCurrentUser);
  }

  function handleLogout() {
    void execute("로그아웃", logout, () => setCurrentUser(null));
  }

  const isBusy = busyAction !== null;

  return (
    <section className="mt-6 rounded-3xl border border-white/10 bg-white/[0.045] p-6 shadow-2xl shadow-black/10">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-violet-300">인증 통합 테스트</p>
          <h2 className="mt-1 text-2xl font-bold">Next.js × FastAPI 세션</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
            브라우저가 HttpOnly 세션 쿠키를 관리합니다. 회원가입 후 로그인하고,
            현재 사용자 조회와 로그아웃을 차례로 확인하세요.
          </p>
        </div>
        <span className="break-all rounded-lg bg-slate-950/60 px-3 py-2 font-mono text-xs text-slate-400">
          {apiBaseUrl}
        </span>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <label className="space-y-2 text-sm text-slate-300">
          <span>이메일</span>
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-4 py-3 outline-none focus:border-violet-300/60"
          />
        </label>
        <label className="space-y-2 text-sm text-slate-300">
          <span>비밀번호</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-4 py-3 outline-none focus:border-violet-300/60"
          />
        </label>
        <label className="space-y-2 text-sm text-slate-300">
          <span>닉네임</span>
          <input
            value={nickname}
            onChange={(event) => setNickname(event.target.value)}
            className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-4 py-3 outline-none focus:border-violet-300/60"
          />
        </label>
        <label className="space-y-2 text-sm text-slate-300">
          <span>역할</span>
          <select
            value={role}
            onChange={(event) =>
              setRole(event.target.value as "consumer" | "creator")
            }
            className="w-full rounded-xl border border-white/10 bg-slate-950/60 px-4 py-3 outline-none focus:border-violet-300/60"
          >
            <option value="consumer">consumer</option>
            <option value="creator">creator</option>
          </select>
        </label>
      </div>

      <div className="mt-5 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={handleRegister}
          disabled={isBusy}
          className="rounded-xl bg-violet-300 px-4 py-3 text-sm font-bold text-slate-950 hover:bg-violet-200 disabled:opacity-50"
        >
          회원가입
        </button>
        <button
          type="button"
          onClick={handleLogin}
          disabled={isBusy}
          className="rounded-xl bg-teal-300 px-4 py-3 text-sm font-bold text-slate-950 hover:bg-teal-200 disabled:opacity-50"
        >
          로그인
        </button>
        <button
          type="button"
          onClick={handleMe}
          disabled={isBusy}
          className="rounded-xl border border-white/15 px-4 py-3 text-sm font-bold hover:border-blue-300/50 hover:bg-blue-300/10 disabled:opacity-50"
        >
          내 정보
        </button>
        <button
          type="button"
          onClick={handleLogout}
          disabled={isBusy}
          className="rounded-xl border border-white/15 px-4 py-3 text-sm font-bold hover:border-rose-300/50 hover:bg-rose-300/10 disabled:opacity-50"
        >
          로그아웃
        </button>
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-slate-950/50 p-4">
          <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
            현재 사용자
          </p>
          <pre className="mt-3 min-h-24 overflow-x-auto whitespace-pre-wrap text-sm text-emerald-300">
            {currentUser
              ? JSON.stringify(currentUser, null, 2)
              : "로그인된 사용자가 없습니다."}
          </pre>
        </div>
        <div className="rounded-2xl border border-white/10 bg-slate-950/50 p-4">
          <p className="text-xs font-bold uppercase tracking-wider text-slate-500">
            마지막 결과
          </p>
          {busyAction && (
            <p className="mt-3 text-sm text-amber-300">{busyAction} 요청 중...</p>
          )}
          {!busyAction && result.kind === "idle" && (
            <p className="mt-3 text-sm text-slate-500">
              버튼을 눌러 인증 흐름을 시작하세요.
            </p>
          )}
          {!busyAction && result.kind === "success" && (
            <div className="mt-3 text-sm text-emerald-300">
              <p>{result.title} 성공</p>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-slate-300">
                {JSON.stringify(result.data, null, 2)}
              </pre>
            </div>
          )}
          {!busyAction && result.kind === "error" && (
            <div className="mt-3 text-sm text-rose-300">
              <p>
                {result.title} 실패
                {result.status ? ` · HTTP ${result.status}` : ""}
              </p>
              <p className="mt-2 text-slate-300">{result.message}</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
