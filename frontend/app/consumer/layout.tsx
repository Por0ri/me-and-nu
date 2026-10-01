import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ConsumerFlowProvider } from "@/components/providers/consumer-flow-provider";
import { ConsumerSessionProvider } from "@/components/providers/consumer-session-provider";
import { ContentNavigationProvider } from "@/components/providers/content-navigation-provider";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";

import "./consumer.css";

export const metadata: Metadata = {
  title: isConsumerApiMode ? "me;nu" : "me;nu Consumer Mock",
  description: isConsumerApiMode
    ? "관심 분야를 선택하고 나에게 맞는 콘텐츠를 만나보세요."
    : "Consumer 화면 확인을 위한 Mock이며, Session·Topic은 같은 탭의 새로고침에서도 유지됩니다.",
};

export default function ConsumerLayout({ children }: { children: ReactNode }) {
  return (
    <div className="consumer-mock">
      <ConsumerSessionProvider>
        <ConsumerFlowProvider>
          <ContentNavigationProvider>{children}</ContentNavigationProvider>
        </ConsumerFlowProvider>
      </ConsumerSessionProvider>
    </div>
  );
}
