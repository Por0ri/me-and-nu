"use client";

import { useState } from "react";

type HealthResponse = {
  status?: string;
  service?: string;
  environment?: string;
  timestamp?: string;
};

type CheckState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "success"; data: HealthResponse; elapsed: number }
  | { kind: "error"; message: string };

const externalApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
const healthUrl = externalApiBaseUrl
  ? `${externalApiBaseUrl}/health`
  : "/api/health";

export default function ApiHealthCheck() {
  const [state, setState] = useState<CheckState>({ kind: "idle" });

  async function checkApi() {
    setState({ kind: "loading" });
    const startedAt = performance.now();

    try {
      const response = await fetch(healthUrl, {
        cache: "no-store",
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}`);
      }

      const data = (await response.json()) as HealthResponse;
      setState({
        kind: "success",
        data,
        elapsed: Math.round(performance.now() - startedAt),
      });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "알 수 없는 오류",
      });
    }
  }

  return (
    <section className="rounded-3xl border border-white/10 bg-white/[0.045] p-6 shadow-2xl shadow-black/10 backdrop-blur">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-teal-300">API 연결 확인</p>
          <h2 className="mt-1 text-xl font-bold">Health Check</h2>
          <p className="mt-2 break-all text-sm text-slate-400">호출 주소: {healthUrl}</p>
        </div>
        <button
          type="button"
          onClick={checkApi}
          disabled={state.kind === "loading"}
          className="rounded-xl bg-teal-300 px-5 py-3 text-sm font-bold text-slate-950 hover:bg-teal-200 disabled:cursor-wait disabled:opacity-60"
        >
          {state.kind === "loading" ? "확인 중..." : "API 호출하기"}
        </button>
      </div>

      <div className="mt-5 min-h-24 rounded-2xl border border-white/10 bg-slate-950/50 p-4 font-mono text-sm">
        {state.kind === "idle" && (
          <p className="text-slate-500">버튼을 눌러 배포 환경의 API 연결을 검사하세요.</p>
        )}
        {state.kind === "loading" && <p className="text-amber-300">요청을 전송했습니다.</p>}
        {state.kind === "success" && (
          <div className="space-y-2 text-emerald-300">
            <p>성공 · {state.elapsed}ms</p>
            <pre className="overflow-x-auto whitespace-pre-wrap text-slate-300">
              {JSON.stringify(state.data, null, 2)}
            </pre>
          </div>
        )}
        {state.kind === "error" && (
          <div className="space-y-2 text-rose-300">
            <p>호출 실패 · {state.message}</p>
            <p className="font-sans text-xs text-slate-400">
              외부 FastAPI를 사용 중이라면 CORS와 HTTPS 설정을 확인하세요.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
