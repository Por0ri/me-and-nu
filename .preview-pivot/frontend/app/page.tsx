import Link from "next/link";
import ApiHealthCheck from "@/components/ApiHealthCheck";
import AuthIntegrationTest from "@/components/AuthIntegrationTest";
import ContentBrowser from "@/components/ContentBrowser";

const checks = [
  ["API 통신", "FastAPI Health API와 CORS 응답 확인"],
  ["세션", "개발용 가입·로그인과 CSRF 확인"],
  ["온보딩", "정책·Topic·Subtopic 선택"],
  ["피드", "내 관심사와 공개 콘텐츠 조회"],
  ["반응", "O/X·좋아요·북마크 설정과 취소"],
];

export default function Home() {
  return (
    <main className="mx-auto min-h-screen w-full max-w-6xl px-5 py-12 sm:px-8 lg:py-20">
      <header className="mb-10">
        <p className="mb-3 text-sm font-bold tracking-[0.24em] text-teal-300">
          ME;NU
        </p>
        <h1 className="max-w-3xl text-4xl font-black tracking-tight sm:text-6xl">
          관심사에서 만나는 글
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-7 text-slate-400 sm:text-lg">
          공개된 콘텐츠를 Topic과 세부 토픽별로 둘러보세요. Agent가 작성한 글은 AI 생성 표시가 붙습니다.
        </p>
      </header>

      <ContentBrowser />

      <section id="api-test" className="mt-16 border-t border-white/10 pt-10">
        <h2 className="text-2xl font-bold">개발용 API 점검</h2>
        <p className="mt-2 mb-6 text-sm text-slate-400">로그인·온보딩과 API 응답을 확인하는 도구입니다. 설정을 마친 뒤 위 피드에서 새로고침을 누르세요.</p>

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
      </section>

      <footer className="mt-10 border-t border-white/10 pt-6 text-sm text-slate-500">
        로컬 Swagger UI는 http://localhost:8000/docs 에서 사용할 수 있습니다.
      </footer>
    </main>
  );
}
