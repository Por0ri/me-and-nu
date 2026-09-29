import Link from "next/link";

import { consumerRoutes } from "@/lib/consumer-routes";

type ConsumerTab = "home" | "saved" | "my";

const TABS: Array<{ id: ConsumerTab; label: string; href: string; ariaLabel: string }> = [
  { id: "home", label: "HOME", href: consumerRoutes.home, ariaLabel: "홈으로 이동" },
  { id: "saved", label: "SAVE", href: consumerRoutes.saved, ariaLabel: "저장 목록으로 이동" },
  { id: "my", label: "MY", href: consumerRoutes.my, ariaLabel: "마이페이지로 이동" },
];

/** 홈·저장·마이페이지가 같이 쓰는 하단 탭. 현재 분야(topicId)를 다음 화면으로 넘긴다. */
export function ConsumerBottomNav({
  current,
  topicId,
}: {
  current: ConsumerTab;
  topicId: string | null;
}) {
  return (
    <nav aria-label="하단 메뉴" className="va-bottom-nav">
      {TABS.map((tab) =>
        tab.id === current ? (
          <span key={tab.id} aria-current="page" className="va-nav-current">
            <span className="va-nav-indicator">{tab.label}</span>
          </span>
        ) : (
          <Link
            key={tab.id}
            href={topicId ? { pathname: tab.href, query: { topicId } } : tab.href}
            aria-label={tab.ariaLabel}
          >
            {tab.label}
          </Link>
        ),
      )}
    </nav>
  );
}
