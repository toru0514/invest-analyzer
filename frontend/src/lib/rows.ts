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
