import Link from "next/link";
import type { ReactNode } from "react";

/** 마이페이지 안쪽 화면 공통 틀: 뒤로가기 + 내용 */
export function MySubPage({ backHref, children }: { backHref: string | { pathname: string; query: Record<string, string> }; children: ReactNode }) {
  return (
    <main className="ui-version-a">
      <div className="va-shell va-tab-page">
        <header className="va-onboarding-header">
          <Link href={backHref} aria-label="마이페이지로 돌아가기" className="inline-flex h-11 w-11 items-center justify-center">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </Link>
        </header>
        {children}
      </div>
    </main>
  );
}
