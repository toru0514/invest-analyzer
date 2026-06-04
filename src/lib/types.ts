// 財務データと投資判断に関する型定義

/** 1年度分の財務サマリー（単位: 百万円。株価指標は倍/％） */
export interface FiscalYear {
  /** 年度（例: "2023") */
  year: string;
  /** 売上高 */
  revenue: number;
  /** 営業利益 */
  operatingIncome: number;
  /** 当期純利益 */
  netIncome: number;
  /** 総資産 */
  totalAssets: number;
  /** 自己資本（純資産） */
  equity: number;
  /** 有利子負債 */
  interestBearingDebt: number;
  /** 営業キャッシュフロー */
  operatingCashFlow: number;
  /** 投資キャッシュフロー（通常マイナス） */
  investingCashFlow: number;
  /** 1株当たり配当（円） */
  dividendPerShare: number;
}

/** 会社マスタ + 複数年度の財務データ */
export interface Company {
  id: string;
  name: string;
  /** 証券コード */
  ticker: string;
  sector: string;
  /** 現在株価（円） */
  sharePrice: number;
  /** 発行済株式数（百万株） */
  sharesOutstanding: number;
  /** 古い年度 → 新しい年度の順 */
  fiscalYears: FiscalYear[];
}

/** 個別指標の評価結果 */
export interface MetricResult {
  key: string;
  label: string;
  /** 実数値 */
  value: number;
  /** 表示用にフォーマット済みの文字列 */
  display: string;
  /** 0〜100 のスコア */
  score: number;
  /** good / neutral / bad */
  rating: Rating;
  /** 評価理由 */
  comment: string;
}

export type Rating = "good" | "neutral" | "bad";

export type Verdict = "buy" | "hold" | "avoid";

/** 会社全体の投資判断結果 */
export interface Analysis {
  companyId: string;
  /** 総合スコア 0〜100 */
  totalScore: number;
  verdict: Verdict;
  metrics: MetricResult[];
}
