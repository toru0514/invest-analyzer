"""evaluation.py の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from evaluation import benchmark, summary_stats
from signals import DEFAULT_CONFIGS


def _df(n=120, step=5.0, seed=1):
    rng = np.random.default_rng(seed)
    close = np.maximum(1000 + np.cumsum(np.full(n, step) + rng.normal(0, 2, n)), 50)
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 3, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_summary_stats_empty():
    s = summary_stats([])
    assert s["n"] == 0 and s["insufficient"] is True
    assert s["expectancy"] is None and s["std_error"] is None


def test_summary_stats_single_trade_no_stderr():
    s = summary_stats([100.0])
    assert s["n"] == 1 and s["std_error"] is None and s["insufficient"] is True


def test_summary_stats_expectancy_and_winrate():
    pnls = [100.0, -50.0, 100.0, -50.0]   # 勝率50%、期待値 = 平均 = 25
    s = summary_stats(pnls)
    assert s["expectancy"] == 25.0
    assert s["win_rate"] == 50.0
    assert s["avg_win"] == 100.0 and s["avg_loss"] == -50.0


def test_summary_stats_insufficient_threshold():
    assert summary_stats([1.0] * 29)["insufficient"] is True
    assert summary_stats([1.0] * 30)["insufficient"] is False


def test_summary_stats_std_error_positive_for_varied():
    s = summary_stats([10.0, -10.0, 20.0, -20.0])
    assert s["std_error"] is not None and s["std_error"] > 0


def test_benchmark_returns_two_baselines():
    hist = {"X.T": _df()}
    b = benchmark(hist, DEFAULT_CONFIGS, buy_threshold=2, sell_threshold=-2,
                  initial_capital=3000.0, warmup_days=35, backtest_days=60)
    assert "buy_hold_pct" in b and "all_signals_pct" in b
    # 上昇トレンドなので buy&hold はプラス
    assert b["buy_hold_pct"] is not None and b["buy_hold_pct"] > 0
