import Link from "next/link";
import { notFound } from "next/navigation";
import { companies, getCompany } from "@/data/companies";
import { analyze, VERDICT_META } from "@/lib/analyze";
import { VerdictBadge } from "@/components/VerdictBadge";
import { ScoreGauge } from "@/components/ScoreGauge";
import { MetricList } from "@/components/MetricList";
import { FinancialTable } from "@/components/FinancialTable";

// モックなので静的生成しておく
export function generateStaticParams() {
  return companies.map((c) => ({ id: c.id }));
}

export default async function CompanyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const company = getCompany(id);
  if (!company) notFound();

  const analysis = analyze(company);
  const meta = VERDICT_META[analysis.verdict];

  return (
    <div>
      <Link
        href="/"
        className="text-sm text-slate-500 hover:text-slate-900"
      >
        ← 一覧へ戻る
      </Link>

      {/* ヘッダー */}
      <div className="mt-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs text-slate-400">
            {company.ticker}・{company.sector}
          </p>
          <h1 className="mt-0.5 text-2xl font-bold tracking-tight">
            {company.name}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            株価 ¥{company.sharePrice.toLocaleString("ja-JP")}・発行済株式数{" "}
            {company.sharesOutstanding.toLocaleString("ja-JP")}百万株
          </p>
        </div>
        <VerdictBadge verdict={analysis.verdict} />
      </div>

      {/* 判定サマリー */}
      <section className="mt-6 flex flex-col items-center gap-5 rounded-2xl border border-slate-200 bg-white p-6 sm:flex-row">
        <ScoreGauge score={analysis.totalScore} />
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">判定: {meta.label}</h2>
            <VerdictBadge verdict={analysis.verdict} size="sm" />
          </div>
          <p className="mt-1 text-sm text-slate-600">{meta.description}</p>
          <p className="mt-3 text-xs text-slate-400">
            8つの財務指標を加重平均した総合スコアです。65以上で「投資推奨」、45〜64で「中立」、44以下で「見送り」と判定します。
          </p>
        </div>
      </section>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {/* 指標スコア */}
        <section className="rounded-2xl border border-slate-200 bg-white p-6">
          <h3 className="mb-2 font-semibold">指標別スコア</h3>
          <MetricList metrics={analysis.metrics} />
        </section>

        {/* 財務諸表 */}
        <section className="rounded-2xl border border-slate-200 bg-white p-6">
          <h3 className="mb-4 font-semibold">決算サマリー（過去3年）</h3>
          <FinancialTable years={company.fiscalYears} />
        </section>
      </div>
    </div>
  );
}
