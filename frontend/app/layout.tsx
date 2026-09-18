import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vercel 배포 점검판",
  description: "Next.js와 Vercel Git 연동을 확인하는 테스트 프로젝트",
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
