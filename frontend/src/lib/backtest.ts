export type BacktestForm = {
  capital: number;
  days: number;
  demo: boolean;
  persist: boolean;
  atrExit: boolean;
  trailAtrMult: number;
  maxHoldDays: number;
  earningsAware: boolean;
  earningsExitDays: number;
};

export type BacktestBody = {
  initial_capital: number;
  days: number;
  demo: boolean;
  persist: boolean;
  exit_mode: "score" | "plan";
  trail_atr_mult?: number;
  max_hold_days?: number;
  earnings_aware?: boolean;
  earnings_exit_days?: number;
};

/** フォーム状態から /backtest のリクエストボディを組み立てる純関数。
 *  trail/time は 0（OFF）のとき省略し、現挙動と同じペイロードにする。 */
export function buildBacktestBody(f: BacktestForm): BacktestBody {
  const body: BacktestBody = {
    initial_capital: f.capital,
    days: f.days,
    demo: f.demo,
    persist: f.persist,
    exit_mode: f.atrExit ? "plan" : "score",
  };
  if (f.trailAtrMult > 0) body.trail_atr_mult = f.trailAtrMult;
  if (f.maxHoldDays > 0) body.max_hold_days = f.maxHoldDays;
  if (f.earningsAware) body.earnings_aware = true;
  if (f.earningsAware && f.earningsExitDays > 0) body.earnings_exit_days = f.earningsExitDays;
  return body;
}
