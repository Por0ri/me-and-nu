"use client";

import { pendingHomeSource } from "@/lib/pivot/home-service";
import { HomeController } from "./home-controller";

export function HomeRoute() {
  return <HomeController source={pendingHomeSource} />;
}
