import type {
  Analysis,
  Company,
  FiscalYear,
  MetricResult,
  Rating,
  Verdict,
} from "@/lib/types";

// ===== 投資判断エンジン（モック版のルールベース）=====
// 各指標を 0〜100 でスコアリングし、加重平均で総合スコアを算出する。
// 実運用では重みや閾値をチューニング、あるいはモデルに差し替える想定。

/** value を [bad, good] 区間で 0〜100 に線形変換（区間外はクランプ） */
function scale(value: number, bad: number, good: number): number {
  if (good === bad) return 50;
  const t = (value - bad) / (good - bad);
  return Math.max(0, Math.min(100, t * 100));
}

function ratingFromScore(score: number): Rating {
  if (score >= 66) return "good";
  if (score >= 40) return "neutral";
  return "bad";
}

const pct = (v: number) => `${v.toFixed(1)}%`;

/** 売上高 年平均成長率（CAGR, %） */
function revenueCagr(years: FiscalYear[]): number {
  const first = years[0];
  const last = years[years.length - 1];
  const n = years.length - 1;
  if (n <= 0 || first.revenue <= 0) return 0;
  return (Math.pow(last.revenue / first.revenue, 1 / n) - 1) * 100;
}

interface MetricDef {
  key: string;
  label: string;
  /** 重み（合計で正規化される） */
  weight: number;
  compute: (c: Company, latest: FiscalYear) => {
    value: number;
    display: string;
    score: number;
    comment: string;
  };
}

const METRICS: MetricDef[] = [
  {
    key: "revenueGrowth",
    label: "売上成長率 (CAGR)",
    weight: 1.2,
    compute: (c) => {
      const v = revenueCagr(c.fiscalYears);
      return {
        value: v,
        display: pct(v),
        score: scale(v, 0, 20),
        comment:
          v >= 15
            ? "高い売上成長を継続している"
            : v >= 5
            ? "緩やかに成長している"
            : "成長が鈍化している",
      };
    },
  },
  {
    key: "operatingMargin",
    label: "営業利益率",
    weight: 1.2,
    compute: (_c, latest) => {
      const v = (latest.operatingIncome / latest.revenue) * 100;
      return {
        value: v,
        display: pct(v),
        score: scale(v, 0, 15),
        comment:
          v >= 12
            ? "高収益体質"
            : v >= 5
            ? "標準的な収益性"
            : "収益性が低い／赤字",
      };
    },
  },
  {
    key: "roe",
    label: "ROE（自己資本利益率）",
    weight: 1.1,
    compute: (_c, latest) => {
      const v = (latest.netIncome / latest.equity) * 100;
      return {
        value: v,
        display: pct(v),
        score: scale(v, 0, 15),
        comment:
          v >= 10
            ? "資本効率が良い（目安8%超）"
            : v >= 5
            ? "平均的な資本効率"
            : "資本効率が低い",
      };
    },
  },
  {
    key: "equityRatio",
    label: "自己資本比率",
    weight: 1.0,
    compute: (_c, latest) => {
      const v = (latest.equity / latest.totalAssets) * 100;
      return {
        value: v,
        display: pct(v),
        score: scale(v, 15, 60),
        comment:
          v >= 50
            ? "財務が非常に健全"
            : v >= 30
            ? "財務は概ね健全"
            : "負債依存度が高い",
      };
    },
  },
  {
    key: "debtToEquity",
    label: "D/Eレシオ（有利子負債/自己資本）",
    weight: 0.9,
    compute: (_c, latest) => {
      const v = latest.interestBearingDebt / latest.equity;
      return {
        value: v,
        display: `${v.toFixed(2)}倍`,
        // 低いほど良いので bad/good を反転
        score: scale(v, 2.0, 0.2),
        comment:
          v <= 0.5
            ? "借入は少なく安全圏"
            : v <= 1.0
            ? "借入水準は許容範囲"
            : "借入が多く財務リスクあり",
      };
    },
  },
  {
    key: "fcfMargin",
    label: "フリーCFマージン",
    weight: 1.0,
    compute: (_c, latest) => {
      const fcf = latest.operatingCashFlow + latest.investingCashFlow;
      const v = (fcf / latest.revenue) * 100;
      return {
        value: v,
        display: pct(v),
        score: scale(v, -5, 12),
        comment:
          v >= 8
            ? "潤沢なフリーキャッシュフロー"
            : v >= 0
            ? "キャッシュ創出はプラス圏"
            : "フリーCFがマイナス",
      };
    },
  },
  {
    key: "per",
    label: "PER（株価収益率）",
    weight: 0.8,
    compute: (c, latest) => {
      const eps = (latest.netIncome * 1_000_000) / (c.sharesOutstanding * 1_000_000);
      const per = eps > 0 ? c.sharePrice / eps : Infinity;
      const display = Number.isFinite(per) ? `${per.toFixed(1)}倍` : "—（赤字）";
      return {
        value: Number.isFinite(per) ? per : 999,
        display,
        // 割安なほど高スコア。15倍前後を中庸に
        score: Number.isFinite(per) ? scale(per, 40, 8) : 0,
        comment: !Number.isFinite(per)
          ? "純利益がマイナスで算出不可"
          : per <= 15
          ? "割安な水準"
          : per <= 25
          ? "妥当な水準"
          : "割高な水準",
      };
    },
  },
  {
    key: "dividendYield",
    label: "配当利回り",
    weight: 0.8,
    compute: (c, latest) => {
      const v = (latest.dividendPerShare / c.sharePrice) * 100;
      return {
        value: v,
        display: pct(v),
        score: scale(v, 0, 4),
        comment:
          v >= 3
            ? "インカム妙味あり"
            : v >= 1
            ? "標準的な配当"
            : "配当はほぼ期待できない",
      };
    },
  },
];

export function analyze(company: Company): Analysis {
  const latest = company.fiscalYears[company.fiscalYears.length - 1];

  const metrics: MetricResult[] = METRICS.map((def) => {
    const r = def.compute(company, latest);
    return {
      key: def.key,
      label: def.label,
      value: r.value,
      display: r.display,
      score: Math.round(r.score),
      rating: ratingFromScore(r.score),
      comment: r.comment,
    };
  });

  const totalWeight = METRICS.reduce((s, m) => s + m.weight, 0);
  const weighted = METRICS.reduce(
    (s, def, i) => s + metrics[i].score * def.weight,
    0
  );
  const totalScore = Math.round(weighted / totalWeight);

  const verdict: Verdict =
    totalScore >= 65 ? "buy" : totalScore >= 45 ? "hold" : "avoid";

  return { companyId: company.id, totalScore, verdict, metrics };
}

export const VERDICT_META: Record<
  Verdict,
  { label: string; description: string; color: string }
> = {
  buy: {
    label: "投資推奨",
    description: "財務・成長・割安度のバランスが良好です。",
    color: "emerald",
  },
  hold: {
    label: "中立",
    description: "強みと弱みが混在。追加調査を推奨します。",
    color: "amber",
  },
  avoid: {
    label: "見送り",
    description: "現時点では投資妙味が乏しい、またはリスクが高い水準です。",
    color: "rose",
  },
};
