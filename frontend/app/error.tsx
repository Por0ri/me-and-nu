"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center px-5">
      <section className="max-w-lg text-center">
        <p className="text-sm font-bold text-rose-300">RUNTIME ERROR</p>
        <h1 className="mt-3 text-4xl font-black">화면 처리 중 오류가 발생했습니다.</h1>
        <button
          type="button"
          onClick={reset}
          className="mt-7 rounded-xl bg-rose-300 px-5 py-3 text-sm font-bold text-slate-950 hover:bg-rose-200"
        >
          다시 시도하기
        </button>
      </section>
    </main>
  );
}
