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
