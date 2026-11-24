import { MemberSearchProvider } from "@/components/admin/member-search-context";

export default function MembersLayout({ children }: { children: React.ReactNode }) {
  return <MemberSearchProvider>{children}</MemberSearchProvider>;
}
