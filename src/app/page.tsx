import Link from "next/link";
import { companies } from "@/data/companies";
import { analyze } from "@/lib/analyze";
import { VerdictBadge } from "@/components/VerdictBadge";

export default function HomePage() {
  const rows = companies
    .map((c) => ({ company: c, analysis: analyze(c) }))
    .sort((a, b) => b.analysis.totalScore - a.analysis.totalScore);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">投資判断ダッシュボード</h1>
        <p className="mt-1 text-sm text-slate-500">
          各社の決算データをスコアリングし、投資すべきかどうかを判定します。総合スコア順に表示。
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(({ company, analysis }) => (
          <Link
            key={company.id}
            href={`/companies/${company.id}`}
            className="group rounded-2xl border border-slate-200 bg-white p-5 transition hover:border-slate-300 hover:shadow-md"
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs text-slate-400">
                  {company.ticker}・{company.sector}
                </p>
                <h2 className="mt-0.5 font-semibold leading-snug text-slate-900 group-hover:underline">
                  {company.name}
                </h2>
              </div>
              <VerdictBadge verdict={analysis.verdict} size="sm" />
            </div>

            <div className="mt-4 flex items-end justify-between">
              <div>
                <p className="text-xs text-slate-400">総合スコア</p>
                <p className="text-3xl font-bold tabular-nums text-slate-900">
                  {analysis.totalScore}
                  <span className="text-base font-normal text-slate-400">
                    {" "}
                    / 100
                  </span>
                </p>
              </div>
              <div className="text-right text-xs text-slate-400">
                <p>株価</p>
                <p className="text-sm font-medium text-slate-700">
                  ¥{company.sharePrice.toLocaleString("ja-JP")}
                </p>
              </div>
            </div>

            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${
                  analysis.totalScore >= 65
                    ? "bg-emerald-500"
                    : analysis.totalScore >= 45
                    ? "bg-amber-500"
                    : "bg-rose-500"
                }`}
                style={{ width: `${analysis.totalScore}%` }}
              />
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
