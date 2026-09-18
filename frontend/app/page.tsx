import Link from "next/link";
import ApiHealthCheck from "@/components/ApiHealthCheck";
import AuthIntegrationTest from "@/components/AuthIntegrationTest";

const checks = [
  ["Git 연결", "브랜치 Push가 자동으로 배포되는지 확인"],
  ["환경 분리", "Preview와 Production의 환경변수 구분"],
  ["API 통신", "FastAPI Health API와 CORS 응답 확인"],
  ["쿠키 인증", "회원가입·로그인·내 정보·로그아웃 확인"],
  ["동적 경로", "직접 접근과 새로고침 시 라우팅 확인"],
];

export default function Home() {
  const deployEnvironment = process.env.NEXT_PUBLIC_DEPLOY_ENV ?? "local";
  const commitSha = process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 7) ?? "local";
  const branch = process.env.VERCEL_GIT_COMMIT_REF ?? "local";

  return (
    <main className="mx-auto min-h-screen w-full max-w-6xl px-5 py-12 sm:px-8 lg:py-20">
      <header className="mb-10">
        <div className="mb-5 flex flex-wrap gap-2 text-xs font-bold">
          <span className="rounded-full border border-teal-300/20 bg-teal-300/10 px-3 py-1.5 text-teal-200">
            환경 · {deployEnvironment}
          </span>
          <span className="rounded-full border border-blue-300/20 bg-blue-300/10 px-3 py-1.5 text-blue-200">
            브랜치 · {branch}
          </span>
          <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-slate-300">
            커밋 · {commitSha}
          </span>
        </div>
        <p className="mb-3 text-sm font-bold tracking-[0.24em] text-teal-300">
          NEXT.JS × VERCEL
        </p>
        <h1 className="max-w-3xl text-4xl font-black tracking-tight sm:text-6xl">
          프론트엔드 배포
          <span className="block text-slate-400">한 화면에서 점검하기</span>
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-7 text-slate-400 sm:text-lg">
          기능 브랜치에서는 Preview URL을, main에서는 Production URL을 확인하세요.
          화면 상단의 환경·브랜치·커밋 값이 배포마다 달라집니다.
        </p>
      </header>

      <section className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        {checks.map(([title, description], index) => (
          <article
            key={title}
            className="rounded-2xl border border-white/10 bg-white/[0.045] p-5"
          >
            <span className="text-xs font-bold text-slate-500">0{index + 1}</span>
            <h2 className="mt-4 font-bold text-slate-100">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
          </article>
        ))}
      </section>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_0.6fr]">
        <ApiHealthCheck />
        <section className="rounded-3xl border border-white/10 bg-white/[0.045] p-6">
          <p className="text-sm font-semibold text-blue-300">라우팅 확인</p>
          <h2 className="mt-1 text-xl font-bold">동적 페이지 열기</h2>
          <p className="mt-3 text-sm leading-6 text-slate-400">
            동적 URL로 이동한 뒤 브라우저를 새로고침해도 같은 페이지가 나타나는지 확인하세요.
          </p>
          <Link
            href="/deployments/preview-001"
            className="mt-6 inline-flex rounded-xl border border-white/15 px-4 py-3 text-sm font-bold text-slate-200 hover:border-blue-300/50 hover:bg-blue-300/10"
          >
            /deployments/preview-001 →
          </Link>
        </section>
      </div>

      <AuthIntegrationTest />

      <footer className="mt-10 border-t border-white/10 pt-6 text-sm text-slate-500">
        Preview에서 검증한 뒤 main에 병합하고 Production 변경을 확인하세요.
      </footer>
    </main>
  );
}
