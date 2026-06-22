"""ペーパートレード・バックテスト。

複数銘柄・仮想資金共有・端株可。各営業日の判定にはその日までのデータのみ使う
（look-ahead bias 回避）。仕様書 §4 の必須成績を返す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from costs import DEFAULT_COST, apply_costs, commission_cost
from signals import (BUY_THRESHOLD, DEFAULT_CONFIGS, DEFAULT_RISK_PCT, SELL_THRESHOLD,
                     build_plan, evaluate, position_size, trailing_stop)

INITIAL_CAPITAL = 3000.0   # 仮想資金（円）
BACKTEST_DAYS = 22         # 評価営業日数（≒1ヶ月）
WARMUP_DAYS = 35           # 指標計算に必要な助走期間


def _regime_at(regime_series, d):
    """日次レジーム系列から d 時点（以前）のレジームを返す。None/未来日は None。"""
    if regime_series is None:
        return None
    v = regime_series.asof(d)
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _rs_at(index_history, rs_params, window, d):
    """評価日 d 時点の相対力強度を返す（index_history/rs_params 未指定で None）。"""
    if index_history is None or rs_params is None:
        return None
    from signals import relative_strength
    return relative_strength(window, index_history,
                             n=int(rs_params.get("period", 20)),
                             scale=float(rs_params.get("scale", 0.10)), asof=d)


def run_backtest(
    histories, configs=None, initial_capital=INITIAL_CAPITAL,
    backtest_days=BACKTEST_DAYS, warmup_days=WARMUP_DAYS,
    buy_threshold=BUY_THRESHOLD, sell_threshold=SELL_THRESHOLD,
    exit_mode="score", cost=None, eval_start_date=None, regime_series=None,
    index_history=None, rs_params=None, risk_pct=DEFAULT_RISK_PCT,
    trail_atr_mult=0.0, max_hold_days=0,
    earnings_map=None, earnings_exit_days=0,
) -> dict:
    """exit_mode='score'（既定）はスコア反転で決済。'plan'（旧'atr'）は提示指値で約定する出口入り。

    cost: {'commission_bps','slippage_bps'}（None で DEFAULT_COST）。
    eval_start_date: 指定すると約定（取引）はこの日以降のみ。指標窓は全履歴を使う（out-of-sample 用）。
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    cost = cost or DEFAULT_COST
    if exit_mode in ("plan", "atr"):
        return _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                                  warmup_days, buy_threshold, sell_threshold, cost,
                                  eval_start_date, regime_series,
                                  index_history=index_history, rs_params=rs_params,
                                  risk_pct=risk_pct,
                                  trail_atr_mult=trail_atr_mult, max_hold_days=max_hold_days,
                                  earnings_map=earnings_map, earnings_exit_days=earnings_exit_days)

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
    eval_dates = [d for d in all_dates if eval_start_date is None or d >= eval_start_date]
    eval_dates = eval_dates[-backtest_days:]

    for d in eval_dates:
        for ticker, df in histories.items():
            window = df[df.index <= d]
            if len(window) < warmup_days:
                continue
            score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold,
                                                regime=_regime_at(regime_series, d),
                                                rs_strength=_rs_at(index_history, rs_params, window, d))
            raw = float(window["close"].iloc[-1])

            if direction == "buy" and cash > 0:
                fill = apply_costs(raw, "buy", cost)
                current_value = holdings[ticker] * fill
                invest = min(per_trade_budget, cash, max(0.0, per_trade_budget - current_value))
                if invest >= 1.0:
                    fee = commission_cost(invest, cost)
                    shares = (invest - fee) / fill
                    total_cost = cost_basis[ticker] * holdings[ticker] + invest
                    holdings[ticker] += shares
                    cost_basis[ticker] = total_cost / holdings[ticker]
                    cash -= invest
                    trades.append({"date": str(pd.Timestamp(d).date()), "ticker": ticker,
                                   "action": "buy", "price": fill, "shares": shares})

            elif direction == "sell" and holdings[ticker] > 0:
                fill = apply_costs(raw, "sell", cost)
                shares = holdings[ticker]
                proceeds = shares * fill
                proceeds -= commission_cost(proceeds, cost)
                pnl = proceeds - cost_basis[ticker] * shares
                cash += proceeds
                closed_pnls.append(pnl)
                trades.append({"date": str(pd.Timestamp(d).date()), "ticker": ticker,
                               "action": "sell", "price": fill, "shares": shares})
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
        score, direction, detail = evaluate(df, configs, buy_threshold, sell_threshold,
                                            regime=_regime_at(regime_series, df.index[-1]),
                                            rs_strength=_rs_at(index_history, rs_params, df, df.index[-1]))
        signal_rows.append({"ticker": ticker, "price": last_close,
                            "score": score, "direction": direction, "detail": detail})

    pnl_amount = final_value - initial_capital
    pnl_pct = pnl_amount / initial_capital * 100
    wins = sum(1 for p in closed_pnls if p > 0)
    win_rate = (wins / len(closed_pnls) * 100) if closed_pnls else None

    max_dd, peak = 0.0, -np.inf
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_dd = min(max_dd, (point["equity"] - peak) / peak)

    return {
        "initial": initial_capital, "final": final_value, "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct, "trade_count": len(trades), "closed_trades": len(closed_pnls),
        "closed_pnls": closed_pnls, "win_rate": win_rate, "max_drawdown_pct": abs(max_dd) * 100,
        "trades": trades, "signals": signal_rows, "equity_curve": equity_curve,
        "exit_mode": "score", "cost": cost, "fill_rate": None,
    }


def _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                       warmup_days, buy_threshold, sell_threshold, cost,
                       eval_start_date, regime_series=None,
                       index_history=None, rs_params=None,
                       risk_pct=DEFAULT_RISK_PCT,
                       trail_atr_mult=0.0, max_hold_days=0,
                       earnings_map=None, earnings_exit_days=0) -> dict:
    """提示指値（build_plan）で約定し、ATR の損切/利確で決済する出口入りシミュレーション。

    検証=提示：作戦ボードと同一の limit_price/stop_price/target_price で約定検証する。
    eval_start_date 指定時は約定をその日以降に限定（指標窓は全履歴）。
    """
    entry_expiry_days = 5
    trades = []
    closed = []          # {pnl, reason, days}
    equity_by_date: dict[str, float] = {}
    signal_rows = []
    final_value = 0.0
    orders_placed = 0
    orders_filled = 0

    for ticker, df in histories.items():
        df = df.sort_index()
        # 決算翌日の窓バー位置（E より厳密に後の最初のバー）。earnings_map 無し→空＝OFF。
        edates = (earnings_map or {}).get(ticker) or []
        gaps_all = sorted({int(df.index.searchsorted(e, side="right")) for e in edates})
        gaps_in = {g for g in gaps_all if g < len(df)}
        cash = initial_capital / max(len(histories), 1)
        shares = 0.0
        entry_price = stop = target = None
        entry_i = None
        entry_atr = None
        high_water = None
        pending = None   # {"limit","stop","target","expires","confidence","atr"}
        start = max(warmup_days, len(df) - backtest_days)

        for i in range(start, len(df)):
            row = df.iloc[i]
            d = str(pd.Timestamp(df.index[i]).date())
            open_, low, high, close = (float(row["open"]), float(row["low"]),
                                       float(row["high"]), float(row["close"]))
            in_window = eval_start_date is None or df.index[i] >= eval_start_date
            is_gap_bar = i in gaps_in
            in_blackout = earnings_exit_days > 0 and any(i < g <= i + earnings_exit_days
                                                         for g in gaps_all)

            # 1) 提示指値の約定（有効期限内に安値が指値に達したら約定・コスト適用）
            #    注: 地合いレジームゲートは発注（意思決定）時に効くため、発注済みの pending は
            #        翌日以降に地合いが悪化しても約定し得る（既存の発注モデルに準拠・v1 の割り切り）。
            if in_window and shares == 0 and pending is not None and cash > 0:
                if low <= pending["limit"]:
                    # 手数料: エントリーは投入現金(cash)、エグジットは総受取(proceeds)に対して控除
                    fill = apply_costs(pending["limit"], "buy", cost)
                    # 全力買い株数の上限。手数料を投入現金全体で見積もる保守的な天井
                    # （手数料率>0 のとき真の上限よりわずかに低いが買い過ぎない。既定は commission_bps=0 で誤差0）。
                    fee = commission_cost(cash, cost)
                    affordable = (cash - fee) / fill
                    desired = position_size(pending["limit"], pending["stop"],
                                            initial_capital, risk_pct,
                                            confidence=pending.get("confidence"))["shares"]
                    if 0 < desired < affordable:
                        shares = desired                        # リスクサイジング（バケット未満）
                        cash -= shares * fill + commission_cost(shares * fill, cost)
                    else:
                        # キャップ＝従来の全力買い。desired<=0（limit<=stop の異常プラン＝build_plan は
                        # 通常 limit>stop を保証）もここに落ち、全力買いにフォールバックする（v1 の割り切り）。
                        shares = affordable
                        cash = 0.0
                    entry_price, stop, target, entry_i = fill, pending["stop"], pending["target"], i
                    entry_atr = pending.get("atr")
                    high_water = high
                    orders_filled += 1
                    trades.append({"date": d, "ticker": ticker, "action": "buy",
                                   "price": fill, "shares": shares})
                    pending = None
                elif i >= pending["expires"]:
                    pending = None   # 期限切れ（約定せず失効）

            # 2) 保有中（エントリー当日を除く）：トレーリング/損切/利確
            if shares > 0 and entry_i is not None and i > entry_i:
                trailing = trail_atr_mult > 0 and entry_atr is not None
                cur_stop = trailing_stop(stop, high_water, entry_atr, trail_atr_mult, "buy") \
                    if trailing else stop
                if low <= cur_stop:
                    if is_gap_bar and open_ < cur_stop:
                        exit_raw, reason = open_, "gap"     # 決算翌日の窓を寄りで約定
                    else:
                        exit_raw, reason = cur_stop, ("trail" if trailing else "stop")
                elif not trailing and high >= target:
                    exit_raw, reason = target, "target"
                else:
                    exit_raw, reason = None, None
                # 決算跨ぎ回避（終値）: 日中トリガが無くブラックアウト内
                if exit_raw is None and in_blackout:
                    exit_raw, reason = close, "earnings"
                # 時間切れ（終値）
                if exit_raw is None and max_hold_days > 0 and (i - entry_i) >= max_hold_days:
                    exit_raw, reason = close, "time"
                if exit_raw is not None:
                    fill = apply_costs(exit_raw, "sell", cost)
                    proceeds = shares * fill
                    proceeds -= commission_cost(proceeds, cost)
                    closed.append({"pnl": proceeds - entry_price * shares,
                                   "reason": reason, "days": i - entry_i})
                    cash += proceeds
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": fill, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None
                    entry_i = None; entry_atr = None; high_water = None
                elif trailing:
                    high_water = max(high_water, high)

            # 3) 当日終値で判定（意思決定）。指標窓は全履歴 df.iloc[:i+1]。
            window = df.iloc[:i + 1]
            if len(window) >= warmup_days:
                score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold,
                                                    regime=_regime_at(regime_series, df.index[i]),
                                                    rs_strength=_rs_at(index_history, rs_params, window, df.index[i]))
                if shares > 0 and direction == "sell":
                    fill = apply_costs(close, "sell", cost)
                    proceeds = shares * fill
                    proceeds -= commission_cost(proceeds, cost)
                    closed.append({"pnl": proceeds - entry_price * shares,
                                   "reason": "signal", "days": i - entry_i})
                    cash += proceeds
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": fill, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None
                    entry_i = None; entry_atr = None; high_water = None
                elif in_window and shares == 0 and direction == "buy":
                    plan = build_plan(window, "buy", score, configs)
                    if plan["limit_price"] and plan["stop_price"] and plan["target_price"]:
                        # 検証=提示：作戦ボードと同一の提示指値で待つ。
                        # 毎営業日の買いシグナルで指値を更新（前日の未約定指値は取消＝新規発注扱い）。
                        pending = {"limit": plan["limit_price"], "stop": plan["stop_price"],
                                   "target": plan["target_price"], "expires": i + entry_expiry_days,
                                   "confidence": detail.get("confidence"), "atr": plan["atr"]}
                        orders_placed += 1

            if in_window:
                equity_by_date[d] = equity_by_date.get(d, 0.0) + cash + shares * close

        last_close = float(df["close"].iloc[-1])
        final_value += cash + shares * last_close
        score, direction, detail = evaluate(df, configs, buy_threshold, sell_threshold,
                                            regime=_regime_at(regime_series, df.index[-1]),
                                            rs_strength=_rs_at(index_history, rs_params, df, df.index[-1]))
        signal_rows.append({"ticker": ticker, "price": last_close, "score": score,
                            "direction": direction, "detail": detail})

    equity_curve = [{"date": d, "equity": equity_by_date[d]} for d in sorted(equity_by_date)]
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
        "initial": initial_capital, "final": final_value,
        "pnl_amount": final_value - initial_capital,
        "pnl_pct": (final_value - initial_capital) / initial_capital * 100,
        "trade_count": len(trades), "closed_trades": len(closed),
        "closed_pnls": pnls, "win_rate": win_rate, "max_drawdown_pct": abs(max_dd) * 100,
        "trades": trades, "signals": signal_rows, "equity_curve": equity_curve,
        "exit_mode": "plan", "cost": cost,
        "fill_rate": (orders_filled / orders_placed) if orders_placed else None,
        "take_profit_count": sum(1 for c in closed if c["reason"] == "target"),
        "stop_loss_count": sum(1 for c in closed if c["reason"] == "stop"),
        "signal_exit_count": sum(1 for c in closed if c["reason"] == "signal"),
        "trail_exit_count": sum(1 for c in closed if c["reason"] == "trail"),
        "time_exit_count": sum(1 for c in closed if c["reason"] == "time"),
        "gap_exit_count": sum(1 for c in closed if c["reason"] == "gap"),
        "earnings_exit_count": sum(1 for c in closed if c["reason"] == "earnings"),
        "avg_holding_days": avg_holding, "risk_reward": risk_reward,
    }
