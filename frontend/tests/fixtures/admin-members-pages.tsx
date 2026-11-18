"use client";

import { useParams, useSearchParams } from "next/navigation";
import { MembersRoute } from "../../components/admin/members-screen";
import { MemberDetailRoute } from "../../components/admin/member-detail-screen";
import { useMemberSearch } from "../../components/admin/member-search-context";
import { memberUnavailable, parseMemberQuery } from "../../lib/admin-members";
import { memberFixtures, selectMemberFixtures } from "./admin-members";
import type { AdminReadState } from "../../lib/admin-content";

function fixtureState<T>(mode: string | null, data: T): AdminReadState<T> {
  if (mode === "loading") return { status: "loading" };
  if (mode === "unavailable") return memberUnavailable;
  if (mode === "forbidden") return { status: "forbidden", message: "테스트 전용: 접근 권한이 없습니다." };
  if (mode === "login-required") return { status: "login-required", message: "테스트 전용: 로그인이 필요합니다." };
  if (mode === "error") return { status: "error", message: "테스트 전용: 조회 실패" };
  if (mode === "not-found") return { status: "not-found", message: "테스트 전용: 회원을 찾을 수 없습니다." };
  return { status: "ready", data };
}
function FixtureBanner() { return <p data-fixture-banner style={{ position: "fixed", bottom: 0, left: 0, zIndex: 10, background: "#27272a", color: "white", padding: "4px 12px", fontSize: 12 }}>테스트 전용 가상 회원 · 운영 데이터 아님</p>; }

export function FixtureMembersPage() {
  const params = useSearchParams(), { search } = useMemberSearch();
  let query;
  try { query = parseMemberQuery(params, search); } catch { return <MembersRoute />; }
  const mode = params.get("fixture");
  const data = selectMemberFixtures(query, mode === "empty" ? [] : mode === "figma" ? memberFixtures.slice(0, 5) : memberFixtures);
  return <><FixtureBanner /><MembersRoute state={fixtureState(mode, data)} statistics={mode === "figma" ? { domainShare: 100, newCount: 25, newShare: 25, newDomainShare: 100, activeCount: 50, activeShare: 50 } : undefined} /></>;
}

export function FixtureMemberDetailPage() {
  const params = useParams<{ memberId: string }>(), search = useSearchParams();
  const member = memberFixtures.find(item => item.id === params.memberId);
  const data = member && search.get("fixture") === "missing" ? { ...member, maskedEmail: "raw-private@example.test", history: null, onboarding: null, notes: null, savedCount: null, likedCount: null } : member;
  return <><FixtureBanner /><MemberDetailRoute state={data ? fixtureState(search.get("fixture"), data) : { status: "not-found", message: "테스트 회원을 찾을 수 없습니다." }} /></>;
}
