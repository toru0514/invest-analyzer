import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";
import NotificationWatcher from "@/components/NotificationWatcher";

export const metadata: Metadata = {
  title: "株価シグナル通知アプリ",
  description: "複数のテクニカル指標で日本株の買い時/売り時を判定・通知（ローカル専用）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <Nav />
        <NotificationWatcher />
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
