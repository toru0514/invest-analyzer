import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Invest Analyzer | 決算で投資判断",
  description: "各社の決算データから投資するかどうかを判定するモックアプリ",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        <div className="min-h-screen">
          <header className="border-b border-slate-200 bg-white">
            <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
              <Link href="/" className="flex items-center gap-2">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-sm font-bold text-white">
                  IA
                </span>
                <span className="text-lg font-semibold tracking-tight">
                  Invest Analyzer
                </span>
              </Link>
              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500">
                MOCK
              </span>
            </div>
          </header>
          <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
          <footer className="mx-auto max-w-5xl px-6 py-10 text-center text-xs text-slate-400">
            ※ 本アプリの判定はモックのルールベースであり、投資勧誘・助言ではありません。
          </footer>
        </div>
      </body>
    </html>
  );
}
