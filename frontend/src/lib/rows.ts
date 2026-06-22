// ダッシュボードの行データ整形（純関数・テスト対象）。
import { Direction, RefreshRow, Signal, WatchItem } from "@/lib/api";

export type Row = {
  id: number;
  ticker: string;
  name: string;
  price: number | null;
  score: number | null;
  direction: Direction | null;
  date: string | null;
  volRatio: number | null;
  weeklyTrend: string | null;
};

export function mergeRows(
  watch: WatchItem[],
  signals: Signal[],
  prices: Record<string, { date: string; close: number }> = {},
): Row[] {
  const latest = new Map<string, Signal>();
  for (const s of signals) {
    if (!latest.has(s.ticker)) latest.set(s.ticker, s); // signals は新しい順
  }
  return watch.map((w) => {
    const s = latest.get(w.ticker);
    const p = prices[w.ticker];
    const vr = s && typeof s.detail?.vol_ratio === "number" ? (s.detail.vol_ratio as number) : null;
    const wt = s && typeof s.detail?.weekly_trend === "string" ? (s.detail.weekly_trend as string) : null;
    return {
      id: w.id,
      ticker: w.ticker,
      name: w.name,
      price: p ? p.close : null,
      score: s ? s.score : null,
      direction: s ? s.direction : null,
      date: s ? s.date : null,
      volRatio: vr,
      weeklyTrend: wt,
    };
  });
}

// 作戦ボード Top N（打ち手6）。confidence 降順 → |score| 降順 → ticker 昇順の決定論順。
// confidence=null は移行前の旧 plan 行のみで生じうる（新規生成行は常に数値）。?? -1 で最下位に置く。
type Rankable = { ticker: string; direction: Direction; score: number; confidence: number | null };

export function rankByConfidence<T extends Rankable>(rows: T[]): T[] {
  return [...rows]
    .filter((r) => r.direction !== "neutral")
    .sort(
      (a, b) =>
        (b.confidence ?? -1) - (a.confidence ?? -1) ||
        Math.abs(b.score) - Math.abs(a.score) ||
        a.ticker.localeCompare(b.ticker),
    );
}

// 「今夜の推奨」は正の量的確信度を持つ actionable のみを採る。confidence が 0/null
// （＝整数scoreはbuy/sellでも連続確信度が確信なし）の行は推奨に載せない。
export function selectTopN<T extends Rankable>(rows: T[], n: number): T[] {
  return n <= 0 ? [] : rankByConfidence(rows).filter((r) => (r.confidence ?? 0) > 0).slice(0, n);
}

// 作戦カードのサイジング表示（打ち手8）。サイジングが無い（旧行/非buy）か entry 価格が無いなら null。
// 不変条件: perform_refresh は limit_price 真値の buy のみ shares/risk_amount を入れる
//（shares 非null ⟹ limit_price 非null）。limit_price も null 条件に含め、投資額0円の誤表示を防ぐ。
export function riskSummary(
  row: { shares: number | null; risk_amount: number | null; limit_price: number | null },
  accountSize: number,
): { shares: number; positionValue: number; riskAmount: number; riskPctOfAccount: number } | null {
  if (row.shares == null || row.risk_amount == null || row.limit_price == null) return null;
  const positionValue = row.shares * row.limit_price;
  const riskPctOfAccount = accountSize > 0 ? (row.risk_amount / accountSize) * 100 : 0;
  return { shares: row.shares, positionValue, riskAmount: row.risk_amount, riskPctOfAccount };
}

export function applyRefresh(rows: Row[], updated: RefreshRow[]): Row[] {
  const byTicker = new Map(updated.map((u) => [u.ticker, u]));
  return rows.map((r) => {
    const u = byTicker.get(r.ticker);
    if (!u) return r;
    const vr = typeof u.detail?.vol_ratio === "number" ? (u.detail.vol_ratio as number) : r.volRatio;
    const wt = typeof u.detail?.weekly_trend === "string" ? (u.detail.weekly_trend as string) : r.weeklyTrend;
    return { ...r, price: u.price, score: u.score, direction: u.direction, date: u.date, volRatio: vr, weeklyTrend: wt };
  });
}

/** 決算警告のしきい値（暦日）。fetch_earnings_days が暦日を返すため暦日基準。
 *  ※ バックテストの earnings_exit_days（取引バー基準）とは別単位。 */
export const EARNINGS_WARN_DAYS = 5;

/** 決算までの日数がしきい値以内なら { days } を返す純関数（それ以外 null）。 */
export function earningsWarning(
  daysToEarnings: number | null,
  threshold = EARNINGS_WARN_DAYS,
): { days: number } | null {
  if (daysToEarnings == null || daysToEarnings < 0 || daysToEarnings > threshold) {
    return null;
  }
  return { days: daysToEarnings };
}
