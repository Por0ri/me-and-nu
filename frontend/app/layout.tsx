import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "me;nu 콘텐츠 피드",
  description: "관심사별 공개 콘텐츠와 영화 Agent 글을 둘러보세요.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
