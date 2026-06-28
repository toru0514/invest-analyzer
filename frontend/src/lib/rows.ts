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

/** 薄商い警告のしきい値（円・平均売買代金/日）。1億円未満を「実約定に難あり」とみなす。
 *  ※ 個人の実約定可能性の目安。設定化は将来。決算の EARNINGS_WARN_DAYS と同型のフロント定数。 */
export const LIQUIDITY_MIN_YEN = 100_000_000;

// 作戦ボード Top N（打ち手6）。confidence 降順 → |score| 降順 → ticker 昇順の決定論順。
// confidence=null は移行前の旧 plan 行のみで生じうる（新規生成行は常に数値）。?? -1 で最下位に置く。
type Rankable = { ticker: string; direction: Direction; score: number; confidence: number | null; avg_turnover?: number | null };

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
// avg_turnover が既知かつ閾値未満の薄商い銘柄も除外する（null=不明は除外しない）。
export function selectTopN<T extends Rankable>(rows: T[], n: number): T[] {
  return n <= 0
    ? []
    : rankByConfidence(rows)
        .filter((r) => (r.confidence ?? 0) > 0)
        .filter((r) => r.avg_turnover == null || r.avg_turnover >= LIQUIDITY_MIN_YEN)
        .slice(0, n);
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

/** 平均売買代金がしきい値未満なら {turnover} を返す純関数（それ以外 null）。
 *  null（不明・旧行）は警告しない＝「不明=非干渉」。 */
export function liquidityWarning(
  avgTurnover: number | null,
  threshold = LIQUIDITY_MIN_YEN,
): { turnover: number } | null {
  if (avgTurnover == null || avgTurnover >= threshold) return null;
  return { turnover: avgTurnover };
}

/** data_health（JSON文字列）を人間可読な注意文の配列に。null/健全/壊れJSON は []。
 *  ai_risks と同様、生データをフロントで表示用に整形する（しきい値はカウント>0）。 */
export function dataHealthWarnings(json: string | null): string[] {
  if (!json) return [];
  let h: { zero_volume_days?: number; gap_days?: number; spike_days?: number };
  try {
    h = JSON.parse(json);
  } catch {
    return [];
  }
  if (!h || typeof h !== "object" || Array.isArray(h)) return [];
  const out: string[] = [];
  if ((h.zero_volume_days ?? 0) > 0) out.push(`出来高0の日が${h.zero_volume_days}日`);
  if ((h.gap_days ?? 0) > 0) out.push(`データ欠損 ${h.gap_days}件`);
  if ((h.spike_days ?? 0) > 0) out.push(`異常な値動き ${h.spike_days}件（データ要確認）`);
  return out;
}

/** 確信度（打ち手6・0-100）の語感ティア。バッジが数値、根拠文が語感で補完する。
 *  打ち手6 の confidence は低位圧縮分布のため high=60 / mid=35（フロント定数・再生成不要で調整可）。 */
export const CONF_HIGH = 60;
export const CONF_MID = 35;

export function confidenceTier(
  confidence: number | null,
): "high" | "mid" | "low" | null {
  if (confidence == null) return null;
  if (confidence >= CONF_HIGH) return "high";
  if (confidence >= CONF_MID) return "mid";
  return "low";
}

const TIER_WORD: Record<"high" | "mid" | "low", string> = {
  high: "高め", mid: "中程度", low: "低め",
};

/** 型ラベル（順張り/逆張り）。direction × weekly_trend の2軸で決める（regime は使わない）。 */
function planTypeLabel(direction: Direction, weekly: string | null): string {
  if (direction === "buy") {
    if (weekly === "up") return "上昇トレンドの押し目買い";
    if (weekly === "flat") return "横ばいでの反発狙い";
    if (weekly === "down") return "下落局面での逆張り（反発狙い）";
    return "押し目買い";
  }
  if (weekly === "down") return "下降トレンドの戻り売り";
  if (weekly === "up") return "上昇中の戻り売り（逆張り）";
  if (weekly === "flat") return "横ばいでの戻り売り";
  return "戻り売り";
}

/** 作戦カードの自然文「根拠」。actionable（buy/sell）のみ。neutral は null。
 *  型ラベル＋後押し要因（地合い・出来高）＋確信度の語感を1文に。確信度は打ち手6 を再利用（再計算しない）。 */
export function planRationale(row: {
  direction: Direction;
  weekly_trend: "up" | "down" | "flat" | null;
  regime: "risk_on" | "neutral" | "risk_off" | null;
  vol_ratio: number | null;
  confidence: number | null;
}): string | null {
  if (row.direction === "neutral") return null;
  const drivers: string[] = [];
  if (row.regime === "risk_on") drivers.push("地合い良好");
  else if (row.regime === "neutral") drivers.push("地合い中立");
  if (row.vol_ratio != null && row.vol_ratio >= 1.5) {
    drivers.push(`出来高${Number(row.vol_ratio.toFixed(1))}倍が後押し`);
  }
  const tier = confidenceTier(row.confidence);
  const parts = [planTypeLabel(row.direction, row.weekly_trend)];
  if (drivers.length) parts.push(drivers.join("・"));
  if (tier) parts.push(`確信度は${TIER_WORD[tier]}`);
  return parts.join("。") + "。";
}
