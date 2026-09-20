import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Commerce Agent · 演示控制台",
  description: "可追溯、可审批的电商智能客服 Agent 演示控制台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
