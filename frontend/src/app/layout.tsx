import type { Metadata } from "next";
import { Noto_Sans_TC } from "next/font/google";
import { Providers } from "./providers";
import "./globals.css";

const notoSansTC = Noto_Sans_TC({
  weight: ["400", "500", "700", "900"],
  display: "swap",
  variable: "--font-noto-sans-tc",
});

export const metadata: Metadata = {
  title: "繪本工作室",
  description: "AI 固定角色兒童著色繪本",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-Hant" className={notoSansTC.variable}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
