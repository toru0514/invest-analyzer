"""run_backtest の約定統一・コスト・fill_rate の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import run_backtest
from signals import DEFAULT_CONFIGS


def _trend_up_df(n=120, start=1000.0, step=5.0, seed=0):
    """緩やかな上昇トレンドの合成OHLCV（買いシグナルと押し目約定が起きやすい）。"""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, step) + rng.normal(0, 2, n))
    close = np.maximum(close, 50)
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 3, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_plan_mode_returns_cost_fillrate_and_closed_pnls():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60)
    assert r["exit_mode"] == "plan"
    assert "fill_rate" in r and (r["fill_rate"] is None or 0.0 <= r["fill_rate"] <= 1.0)
    assert "cost" in r and r["cost"]["slippage_bps"] == 10.0
    assert isinstance(r["closed_pnls"], list)


def test_atr_is_alias_of_plan():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="atr", backtest_days=60)
    assert r["exit_mode"] == "plan"   # atr は plan のエイリアス


def test_cost_reduces_pnl_vs_zero_cost():
    hist = {"X.T": _trend_up_df()}
    zero = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                        cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    costly = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                          cost={"commission_bps": 0.0, "slippage_bps": 50.0})
    # トレードが発生していれば、スリッページの大きい方が損益は小さい（同数量比較は近似）
    if zero["closed_trades"] > 0 and costly["closed_trades"] > 0:
        assert costly["pnl_pct"] <= zero["pnl_pct"]


def test_score_mode_applies_cost_and_returns_closed_pnls():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="score", backtest_days=60)
    assert r["exit_mode"] == "score"
    assert isinstance(r["closed_pnls"], list)
    assert "cost" in r


def test_eval_start_date_restricts_trading_window():
    df = _trend_up_df()
    hist = {"X.T": df}
    split = df.index[int(len(df) * 0.7)]
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan",
                     backtest_days=len(df), eval_start_date=split)
    # 約定はすべて split 以降
    for t in r["trades"]:
        assert pd.Timestamp(t["date"]) >= split.normalize()
