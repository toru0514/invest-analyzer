"""ペーパートレード・バックテスト。

複数銘柄・仮想資金共有・端株可。各営業日の判定にはその日までのデータのみ使う
（look-ahead bias 回避）。仕様書 §4 の必須成績を返す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signals import BUY_THRESHOLD, DEFAULT_CONFIGS, SELL_THRESHOLD, evaluate

INITIAL_CAPITAL = 3000.0   # 仮想資金（円）
BACKTEST_DAYS = 22         # 評価営業日数（≒1ヶ月）
WARMUP_DAYS = 35           # 指標計算に必要な助走期間


def run_backtest(
    histories: dict[str, pd.DataFrame],
    configs=None,
    initial_capital: float = INITIAL_CAPITAL,
    backtest_days: int = BACKTEST_DAYS,
    warmup_days: int = WARMUP_DAYS,
    buy_threshold: int = BUY_THRESHOLD,
    sell_threshold: int = SELL_THRESHOLD,
) -> dict:
    if configs is None:
        configs = DEFAULT_CONFIGS

    n_tickers = len(histories)
    cash = initial_capital
    holdings = {t: 0.0 for t in histories}
    cost_basis = {t: 0.0 for t in histories}
    per_trade_budget = initial_capital / max(n_tickers, 1)

    trades = []
    closed_pnls = []
    equity_curve = []
    signal_rows = []

    all_dates = sorted(set().union(*[set(df.index) for df in histories.values()]))
    eval_dates = all_dates[-backtest_days:]

    for d in eval_dates:
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if len(window) < warmup_days:
                continue
            score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold)
            price = float(window["close"].iloc[-1])

            if direction == "buy" and cash > 0:
                current_value = holdings[ticker] * price
                invest = min(per_trade_budget, cash, max(0.0, per_trade_budget - current_value))
                if invest >= 1.0:
                    shares = invest / price
                    total_cost = cost_basis[ticker] * holdings[ticker] + invest
                    holdings[ticker] += shares
                    cost_basis[ticker] = total_cost / holdings[ticker]
                    cash -= invest
                    trades.append({"date": str(pd.Timestamp(d).date()), "ticker": ticker,
                                   "action": "buy", "price": price, "shares": shares})

            elif direction == "sell" and holdings[ticker] > 0:
                shares = holdings[ticker]
                pnl = (price - cost_basis[ticker]) * shares
                cash += shares * price
                closed_pnls.append(pnl)
                trades.append({"date": str(pd.Timestamp(d).date()), "ticker": ticker,
                               "action": "sell", "price": price, "shares": shares})
                holdings[ticker] = 0.0
                cost_basis[ticker] = 0.0

        equity = cash
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if not window.empty:
                equity += holdings[ticker] * float(window["close"].iloc[-1])
        equity_curve.append({"date": str(pd.Timestamp(d).date()), "equity": equity})

    final_value = cash
    for ticker, df in histories.items():
        last_close = float(df["close"].iloc[-1])
        final_value += holdings[ticker] * last_close
        score, direction, detail = evaluate(df, configs, buy_threshold, sell_threshold)
        signal_rows.append({"ticker": ticker, "price": last_close,
                            "score": score, "direction": direction, "detail": detail})

    pnl_amount = final_value - initial_capital
    pnl_pct = pnl_amount / initial_capital * 100
    wins = sum(1 for p in closed_pnls if p > 0)
    win_rate = (wins / len(closed_pnls) * 100) if closed_pnls else None

    max_dd = 0.0
    peak = -np.inf
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = min(max_dd, (point["equity"] - peak) / peak)
    max_dd_pct = abs(max_dd) * 100

    return {
        "initial": initial_capital,
        "final": final_value,
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
        "trade_count": len(trades),
        "closed_trades": len(closed_pnls),
        "win_rate": win_rate,
        "max_drawdown_pct": max_dd_pct,
        "trades": trades,
        "signals": signal_rows,
        "equity_curve": equity_curve,
    }
