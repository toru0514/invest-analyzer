// Python API（FastAPI / :8000）クライアント

// 既定は「表示中のページと同じホストの :8000」を指す。
// localhost / LAN IP / Tailscale 名のどれで開いても、その同じホストの backend を叩くので、
// スマホから Tailscale 経由で開いたときに localhost がスマホ自身を指してしまう問題を回避できる。
// 明示的に上書きしたい場合は NEXT_PUBLIC_API_BASE を優先する。
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (typeof window !== "undefined"
    ? `http://${window.location.hostname}:8000`
    : "http://localhost:8000");

export type WatchItem = {
  id: number;
  ticker: string;
  name: string;
  enabled: number;
  created_at: string;
};

export type Direction = "buy" | "sell" | "neutral";

export type Signal = {
  id: number;
  ticker: string;
  date: string;
  score: number;
  direction: Direction;
  detail: Record<string, number | string>;
  notified: number;
};

export type RefreshRow = {
  id: number;
  ticker: string;
  date: string;
  price: number;
  score: number;
  direction: Direction;
  detail: Record<string, number | string>;
};

export type SignalConfig = {
  id: number;
  ticker: string | null;
  rule_type: string;
  params: Record<string, unknown>;
  weight: number;
  enabled: number;
};

export type AppSettings = {
  buy_threshold: number;
  sell_threshold: number;
  scheduler_enabled: boolean;
  scheduler_time: string;
  scheduler_demo: boolean;
  scheduler_skip_holidays: boolean;
  top_n: number;
  account_size: number;
  risk_pct: number;
};

export type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type PlanRow = {
  id: number;
  ticker: string;
  plan_date: string;
  direction: Direction;
  score: number;
  vol_ratio: number | null;
  weekly_trend: "up" | "down" | "flat" | null;
  limit_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  shares: number | null;
  risk_amount: number | null;
  rationale: string | null;
  confidence: number | null;
  regime: "risk_on" | "neutral" | "risk_off" | null;  // 打ち手11で永続化済み・/plan は SELECT * で配信
  days_to_earnings: number | null;
  avg_turnover: number | null;   // 平均売買代金（円・直近20日）。打ち手12
  data_health: string | null;    // JSON: {zero_volume_days,gap_days,spike_days}。健全/旧行は null
  ai_summary: string | null;
  ai_confidence: number | null;
  ai_risks: string | null; // JSON文字列化された string[]
  created_at: string;
};

export type PlanResponse = { plan_date: string | null; rows: PlanRow[]; failed?: string[] };

export type Holding = { ticker: string; shares: number; avg_cost: number };

export type SweepRow = {
  threshold: number;
  pnl_pct: number;
  expectancy: number | null;
  win_rate: number | null;
  trade_count: number;
};
export type ContribRow = { rule_type: string; pnl_without: number; delta: number };
export type Significance = {
  n: number; expectancy: number | null; std_error: number | null; win_rate: number | null;
  avg_win: number | null; avg_loss: number | null; insufficient: boolean;
};
export type OptimizeResponse = {
  chosen_params: { threshold: number; exit_mode: string };
  in_sample: {
    sample: "in_sample"; sweep: SweepRow[]; best: SweepRow | null;
    baseline_pnl_pct: number; contributions: ContribRow[];
    pnl_pct: number; expectancy: number | null; trade_count: number; win_rate: number | null;
  };
  out_of_sample: {
    sample: "out_of_sample"; pnl_pct: number; expectancy: number | null;
    win_rate: number | null; trade_count: number; fill_rate: number | null;
  };
  overfit_gap: number;
  significance: Significance;
  benchmark: { buy_hold_pct: number | null; all_signals_pct: number };
  split_date: string | null;
  failed: string[];
  tickers: string[];
};

export type BacktestResult = {
  initial: number;
  final: number;
  pnl_amount: number;
  pnl_pct: number;
  trade_count: number;
  closed_trades: number;
  win_rate: number | null;
  max_drawdown_pct: number;
  trades: { date: string; ticker: string; action: string; price: number; shares: number }[];
  signals: { ticker: string; price: number; score: number; direction: Direction; detail: Record<string, number | string> }[];
  equity_curve: { date: string; equity: number }[];
  exit_mode?: "score" | "plan";
  // exit_mode === "plan"（出口入りシミュレーション・強化J）でのみ返る追加成績
  take_profit_count?: number;
  stop_loss_count?: number;
  signal_exit_count?: number;
  trail_exit_count?: number;
  time_exit_count?: number;
  gap_exit_count?: number;
  earnings_exit_count?: number;
  avg_holding_days?: number | null;
  risk_reward?: number | null;
  cost?: { commission_bps: number; slippage_bps: number };
  fill_rate?: number | null;
  significance?: Significance;
  benchmark?: { buy_hold_pct: number | null; all_signals_pct: number };
  failed?: string[];
};

export type PerfRow = {
  type: string;
  n_plans: number;
  n_filled: number;
  fill_rate: number | null;   // 0..1
  n_resolved: number;
  win_rate: number | null;    // 0..100
  avg_r: number | null;       // 建玉リスク1単位あたり損益（期待値R）
  avg_days: number | null;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getWatchlist: () => req<WatchItem[]>("/watchlist"),
  // name 省略時はサーバが内蔵マスタ／yfinance から自動解決する
  addWatch: (ticker: string, name = "") =>
    req<WatchItem>("/watchlist", { method: "POST", body: JSON.stringify({ ticker, name }) }),
  deleteWatch: (id: number) =>
    req<{ deleted: number }>(`/watchlist/${id}`, { method: "DELETE" }),
  searchStocks: (q: string) =>
    req<{ ticker: string; name: string }[]>(`/stocks/search?q=${encodeURIComponent(q)}`),

  getConfig: () => req<SignalConfig[]>("/config"),
  updateConfig: (updates: { id: number; weight?: number; enabled?: boolean; params?: Record<string, unknown> }[]) =>
    req<SignalConfig[]>("/config", { method: "PUT", body: JSON.stringify({ updates }) }),
  addConfig: (body: { rule_type: string; ticker?: string | null; params?: Record<string, unknown>; weight?: number; enabled?: boolean }) =>
    req<{ id: number }>("/config", { method: "POST", body: JSON.stringify(body) }),
  deleteConfig: (id: number) =>
    req<{ deleted: number }>(`/config/${id}`, { method: "DELETE" }),

  getSettings: () => req<AppSettings>("/settings"),
  updateSettings: (patch: Partial<AppSettings>) =>
    req<AppSettings>("/settings", { method: "PUT", body: JSON.stringify(patch) }),

  getSignals: (ticker?: string, limit = 100) =>
    req<Signal[]>(`/signals?${ticker ? `ticker=${encodeURIComponent(ticker)}&` : ""}limit=${limit}`),
  getUnnotified: () => req<Signal[]>("/signals/unnotified"),
  markNotified: (ids: number[]) =>
    req<{ marked: number[] }>("/signals/mark_notified", { method: "POST", body: JSON.stringify({ ids }) }),

  getPrices: (ticker: string) => req<Candle[]>(`/prices/${encodeURIComponent(ticker)}`),
  getLatestPrices: () =>
    req<Record<string, { date: string; close: number }>>("/prices_latest"),

  refresh: (demo: boolean) =>
    req<{ updated: RefreshRow[]; failed: string[]; note: string | null }>(
      `/refresh?demo=${demo}`, { method: "POST" }),

  backtest: (body: { tickers?: string[]; initial_capital?: number; days?: number; demo?: boolean; persist?: boolean; exit_mode?: "score" | "plan"; period?: string; trail_atr_mult?: number; max_hold_days?: number; earnings_aware?: boolean; earnings_exit_days?: number }) =>
    req<BacktestResult>("/backtest", { method: "POST", body: JSON.stringify(body) }),

  getPlan: (date?: string) =>
    req<PlanResponse>(`/plan${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  generatePlan: (demo: boolean) =>
    req<PlanResponse>(`/plan/generate?demo=${demo}`, { method: "POST" }),

  optimize: (body: { demo?: boolean; tickers?: string[]; split_ratio?: number; period?: string }) =>
    req<OptimizeResponse>("/optimize", { method: "POST", body: JSON.stringify(body) }),

  getHoldings: () => req<Holding[]>("/holdings"),
  saveHolding: (b: { ticker: string; shares: number; avg_cost: number }) =>
    req<unknown>("/holdings", { method: "PUT", body: JSON.stringify(b) }),
  deleteHolding: (ticker: string) =>
    req<{ deleted: string }>(`/holdings/${encodeURIComponent(ticker)}`, { method: "DELETE" }),

  getPerformance: () => req<{ summary: PerfRow[] }>("/performance"),
};
