import Link from "next/link";

type DeploymentPageProps = {
  params: Promise<{ id: string }>;
};

export default async function DeploymentPage({ params }: DeploymentPageProps) {
  const { id } = await params;

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl items-center px-5 py-12">
      <section className="w-full rounded-3xl border border-white/10 bg-white/[0.045] p-7 sm:p-10">
        <p className="text-sm font-bold text-blue-300">동적 경로 테스트 성공</p>
        <h1 className="mt-3 break-all text-4xl font-black">{id}</h1>
        <p className="mt-5 leading-7 text-slate-400">
          주소창에서 이 페이지를 새로고침해 보세요. 정상적으로 다시 표시되면 동적 경로가
          배포 환경에서도 처리되고 있는 것입니다.
        </p>
        <Link
          href="/"
          className="mt-8 inline-flex rounded-xl bg-blue-300 px-5 py-3 text-sm font-bold text-slate-950 hover:bg-blue-200"
        >
          점검판으로 돌아가기
        </Link>
      </section>
    </main>
  );
}
