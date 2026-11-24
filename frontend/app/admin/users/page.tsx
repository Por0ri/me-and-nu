import { Suspense } from "react";
import { MembersRoute } from "@/components/admin/members-screen";

export default function MembersPage() {
  return <Suspense fallback={<p role="status">회원 화면을 준비 중입니다</p>}><MembersRoute /></Suspense>;
}
