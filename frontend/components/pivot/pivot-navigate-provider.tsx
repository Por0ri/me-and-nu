"use client";

import { useRouter } from "next/navigation";
import { useCallback, type ReactNode } from "react";
import { PivotNavigateContext } from "./home-screen";

/** 하단바 TALK 처럼 다른 pivot 페이지로 옮겨 갈 때 쓰는 이동 함수를 채운다 (새로 불러오지 않고 옮겨 간다).
 *  home-screen.tsx 는 테스트가 그대로 읽어서 next/navigation 을 직접 쓰지 않는다. */
export function PivotNavigateProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const navigate = useCallback((path: string) => router.push(path), [router]);
  return <PivotNavigateContext.Provider value={navigate}>{children}</PivotNavigateContext.Provider>;
}
