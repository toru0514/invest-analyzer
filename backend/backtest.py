"""ペーパートレード・バックテスト。

複数銘柄・仮想資金共有・端株可。各営業日の判定にはその日までのデータのみ使う
（look-ahead bias 回避）。仕様書 §4 の必須成績を返す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signals import BUY_THRESHOLD, DEFAULT_CONFIGS, SELL_THRESHOLD, build_plan, evaluate

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
    exit_mode: str = "score",
) -> dict:
    """exit_mode='score'（既定）はスコア反転で決済。'atr' は追補版 強化J の出口入り。"""
    if configs is None:
        configs = DEFAULT_CONFIGS

    if exit_mode == "atr":
        return _run_backtest_atr(histories, configs, initial_capital, backtest_days,
                                 warmup_days, buy_threshold, sell_threshold)

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
        "exit_mode": "score",
    }


def _run_backtest_atr(histories, configs, initial_capital, backtest_days,
                      warmup_days, buy_threshold, sell_threshold) -> dict:
    """追補版 強化J: 提案指値で約定し、ATR の損切/利確ラインで決済する出口入りシミュレーション。

    銘柄ごとに資金を等分（initial/銘柄数）し、各銘柄は同時に1ポジションのみ持つ簡易モデル。
    - エントリー: buy シグナルの翌営業日、その日の安値が提案指値に達したら指値で約定。
    - 決済: 保有中に当日安値が損切ラインに達したら損切、当日高値が利確ラインに達したら利確。
      逆方向（sell）シグナルが出たらその日の終値で決済。
    - look-ahead 回避: 判定は当日終値まで、執行は翌日以降の OHLC のみ参照。
    """
    n = len(histories)
    budget = initial_capital / max(n, 1)
    trades = []
    closed = []          # {pnl, reason, days}
    equity_by_date: dict[str, float] = {}
    signal_rows = []
    final_value = 0.0

    for ticker, df in histories.items():
        df = df.sort_index()
        cash = budget
        shares = 0.0
        entry_price = stop = target = None
        entry_i = None
        pending = None   # {"limit","stop","target","expires"} 押し目指値（GTC）
        start = max(warmup_days, len(df) - backtest_days)

        for i in range(start, len(df)):
            row = df.iloc[i]
            d = str(pd.Timestamp(df.index[i]).date())
            low, high, close = float(row["low"]), float(row["high"]), float(row["close"])

            # 1) 押し目指値の約定（買いシグナル翌日以降・有効期限内に安値が指値に達したら約定）
            if shares == 0 and pending is not None and cash > 0:
                if low <= pending["limit"]:
                    fill = pending["limit"]
                    shares = cash / fill
                    entry_price, stop, target, entry_i = fill, pending["stop"], pending["target"], i
                    cash = 0.0
                    trades.append({"date": d, "ticker": ticker, "action": "buy",
                                   "price": fill, "shares": shares})
                    pending = None
                elif i >= pending["expires"]:
                    pending = None   # 期限切れ（約定せず失効）

            # 2) 保有中（エントリー当日を除く）: 損切優先で stop/target をチェック
            if shares > 0 and entry_i is not None and i > entry_i:
                exit_price, reason = (stop, "stop") if low <= stop else \
                    (target, "target") if high >= target else (None, None)
                if exit_price is not None:
                    closed.append({"pnl": (exit_price - entry_price) * shares,
                                   "reason": reason, "days": i - entry_i})
                    cash += shares * exit_price
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": exit_price, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None; entry_i = None

            # 3) 当日終値で判定（意思決定）
            window = df.iloc[:i + 1]
            if len(window) >= warmup_days:
                score, direction, _ = evaluate(window, configs, buy_threshold, sell_threshold)
                if shares > 0 and direction == "sell":
                    closed.append({"pnl": (close - entry_price) * shares,
                                   "reason": "signal", "days": i - entry_i})
                    cash += shares * close
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": close, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None; entry_i = None
                elif shares == 0 and direction == "buy":
                    plan = build_plan(window, "buy", score, configs)
                    atr = plan["atr"]
                    if atr and plan["stop_price"] and plan["target_price"]:
                        # シミュレーションの約定はハーフATRの押し目（reachable）で待つ。
                        # 作戦ボードの提案指値（サポート基準）は人間向けの別物。
                        pending = {"limit": close - 0.5 * atr, "stop": plan["stop_price"],
                                   "target": plan["target_price"], "expires": i + 5}

            equity_by_date[d] = equity_by_date.get(d, 0.0) + cash + shares * close

        last_close = float(df["close"].iloc[-1])
        final_value += cash + shares * last_close
        score, direction, detail = evaluate(df, configs, buy_threshold, sell_threshold)
        signal_rows.append({"ticker": ticker, "price": last_close, "score": score,
                            "direction": direction, "detail": detail})

    equity_curve = [{"date": d, "equity": equity_by_date[d]}
                    for d in sorted(equity_by_date)]
    pnls = [c["pnl"] for c in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = (len(wins) / len(pnls) * 100) if pnls else None
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(losses) / len(losses)) if losses else 0.0
    risk_reward = (avg_win / abs(avg_loss)) if losses and avg_loss != 0 else None
    avg_holding = (sum(c["days"] for c in closed) / len(closed)) if closed else None

    max_dd, peak = 0.0, -np.inf
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = min(max_dd, (point["equity"] - peak) / peak)

    return {
        "initial": initial_capital,
        "final": final_value,
        "pnl_amount": final_value - initial_capital,
        "pnl_pct": (final_value - initial_capital) / initial_capital * 100,
        "trade_count": len(trades),
        "closed_trades": len(closed),
        "win_rate": win_rate,
        "max_drawdown_pct": abs(max_dd) * 100,
        "trades": trades,
        "signals": signal_rows,
        "equity_curve": equity_curve,
        "exit_mode": "atr",
        "take_profit_count": sum(1 for c in closed if c["reason"] == "target"),
        "stop_loss_count": sum(1 for c in closed if c["reason"] == "stop"),
        "signal_exit_count": sum(1 for c in closed if c["reason"] == "signal"),
        "avg_holding_days": avg_holding,
        "risk_reward": risk_reward,
    }
