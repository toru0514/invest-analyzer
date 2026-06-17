"""out-of-sample 評価層：ホールドアウト2段構え・統計サマリ・ベンチマーク。"""
from __future__ import annotations

import statistics

from costs import DEFAULT_COST

# in-sample で探索する既定グリッド（閾値のみ・exit_mode は plan 固定）
DEFAULT_GRID: dict[str, list[int]] = {"threshold": [2, 3, 4]}

MIN_TRADES = 30   # これ未満は統計的に不十分（誤差範囲）


def summary_stats(pnls: list[float], min_trades: int = MIN_TRADES) -> dict:
    """クローズ済みトレード損益（コスト込み）から統計サマリを返す。"""
    n = len(pnls)
    if n == 0:
        return {"n": 0, "expectancy": None, "std_error": None, "win_rate": None,
                "avg_win": None, "avg_loss": None, "insufficient": True}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "n": n,
        "expectancy": sum(pnls) / n,                       # 1トレードあたり期待損益
        "std_error": (statistics.stdev(pnls) / (n ** 0.5)) if n >= 2 else None,
        "win_rate": len(wins) / n * 100,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "insufficient": n < min_trades,
    }


def benchmark(histories, configs, *, buy_threshold, sell_threshold,
              initial_capital, warmup_days, backtest_days, cost=None) -> dict:
    """評価窓のベンチマーク2種。(a) ユニバース等加重 buy&hold、(b) 全シグナル等加重（素のシグナル運用）。"""
    from backtest import run_backtest

    cost = cost or DEFAULT_COST
    # (a) buy&hold：評価窓（末尾 backtest_days 日）の頭→末リターンを等加重平均
    rets = []
    for df in histories.values():
        win = df.sort_index().tail(backtest_days)
        if len(win) >= 2 and float(win["close"].iloc[0]) > 0:
            rets.append(float(win["close"].iloc[-1]) / float(win["close"].iloc[0]) - 1.0)
    buy_hold_pct = (sum(rets) / len(rets) * 100) if rets else None

    # (b) 全シグナル等加重（選別なし）：score モードの素のシグナル運用（コスト込み）
    naive = run_backtest(histories, configs=configs, initial_capital=initial_capital,
                         backtest_days=backtest_days, warmup_days=warmup_days,
                         buy_threshold=buy_threshold, sell_threshold=sell_threshold,
                         exit_mode="score", cost=cost)
    return {"buy_hold_pct": buy_hold_pct, "all_signals_pct": naive["pnl_pct"]}
