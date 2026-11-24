import type { Metadata } from "next";
import { DebateCreate } from "@/components/debate/debate-create";

export const metadata: Metadata = { title: "me;nu 토론 주제 올리기" };

export default function Page() {
  return <DebateCreate />;
}
