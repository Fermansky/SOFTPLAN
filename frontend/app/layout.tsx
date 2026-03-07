import "./globals.css";
import type { Metadata } from "next";
import { ReactNode } from "react";
import { Manrope, IBM_Plex_Mono } from "next/font/google";

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-ui"
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono"
});

export const metadata: Metadata = {
  title: "Softplan",
  description: "Softplan frontend scaffold"
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className={`${manrope.variable} ${plexMono.variable}`}>{children}</body>
    </html>
  );
}
