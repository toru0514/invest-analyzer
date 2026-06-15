import Link from "next/link";

const links = [
  { href: "/", label: "ダッシュボード" },
  { href: "/plan", label: "作戦ボード" },
  { href: "/settings", label: "設定" },
  { href: "/simulation", label: "シミュレーション" },
];

export default function Nav() {
  return (
    <header className="border-b bg-white">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
        <Link href="/" className="font-bold text-slate-900">
          📈 株価シグナル通知
        </Link>
        <nav className="flex gap-4 text-sm">
          {links.map((l) => (
            <Link key={l.href} href={l.href} className="text-slate-600 hover:text-slate-900">
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
