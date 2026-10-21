import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-5">
      <section className="text-center">
        <p className="text-sm font-bold text-rose-300">404 ROUTE CHECK</p>
        <h1 className="mt-3 text-4xl font-black">페이지를 찾을 수 없습니다.</h1>
        <Link href="/" className="mt-7 inline-flex text-sm font-bold text-teal-300 hover:text-teal-200">
          점검판으로 돌아가기 →
        </Link>
      </section>
    </main>
  );
}
