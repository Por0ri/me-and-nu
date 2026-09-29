import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ConsumerFlowProvider } from "@/components/providers/consumer-flow-provider";
import { ConsumerSessionProvider } from "@/components/providers/consumer-session-provider";

import "./consumer.css";

export const metadata: Metadata = {
  title: "me;nu Consumer Mock",
  description: "Consumer 화면 확인을 위한 Mock이며, Session·Topic은 같은 탭의 새로고침에서도 유지됩니다.",
};

export default function ConsumerLayout({ children }: { children: ReactNode }) {
  return (
    <div className="consumer-mock">
      <ConsumerSessionProvider>
        <ConsumerFlowProvider>{children}</ConsumerFlowProvider>
      </ConsumerSessionProvider>
    </div>
  );
}
