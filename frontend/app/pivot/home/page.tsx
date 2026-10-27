import type { Metadata } from "next";
import { HomeRoute } from "@/components/pivot/home-route";

export const metadata: Metadata = { title: "me;nu 홈" };

export default function Page() {
  return <HomeRoute />;
}
