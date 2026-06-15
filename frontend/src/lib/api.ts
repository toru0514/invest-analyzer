// Python API（FastAPI / :8000）クライアント

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

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

export type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
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
  failed?: string[];
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
  addWatch: (ticker: string, name: string) =>
    req<WatchItem>("/watchlist", { method: "POST", body: JSON.stringify({ ticker, name }) }),
  deleteWatch: (id: number) =>
    req<{ deleted: number }>(`/watchlist/${id}`, { method: "DELETE" }),

  getConfig: () => req<SignalConfig[]>("/config"),
  updateConfig: (updates: { id: number; weight?: number; enabled?: boolean; params?: Record<string, unknown> }[]) =>
    req<SignalConfig[]>("/config", { method: "PUT", body: JSON.stringify({ updates }) }),

  getSignals: (ticker?: string, limit = 100) =>
    req<Signal[]>(`/signals?${ticker ? `ticker=${encodeURIComponent(ticker)}&` : ""}limit=${limit}`),
  getUnnotified: () => req<Signal[]>("/signals/unnotified"),
  markNotified: (ids: number[]) =>
    req<{ marked: number[] }>("/signals/mark_notified", { method: "POST", body: JSON.stringify({ ids }) }),

  getPrices: (ticker: string) => req<Candle[]>(`/prices/${encodeURIComponent(ticker)}`),

  refresh: (demo: boolean) =>
    req<{ updated: RefreshRow[]; failed: string[]; note: string | null }>(
      `/refresh?demo=${demo}`, { method: "POST" }),

  backtest: (body: { tickers?: string[]; initial_capital?: number; days?: number; demo?: boolean; persist?: boolean }) =>
    req<BacktestResult>("/backtest", { method: "POST", body: JSON.stringify(body) }),
};
