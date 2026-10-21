"use client";

import { pendingHomeSource } from "@/lib/pivot/home-service";
import { pendingDetailSource } from "@/lib/pivot/detail-service";
import { Suspense } from "react";
import { HomeDetailExperience } from "./home-detail-experience";

export function HomeRoute() {
  return <Suspense fallback={<p role="status">콘텐츠를 불러오고 있어요.</p>}><HomeDetailExperience homeSource={pendingHomeSource} detailSource={pendingDetailSource} /></Suspense>;
}
