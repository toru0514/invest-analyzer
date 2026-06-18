"""run_backtest の約定統一・コスト・fill_rate の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import run_backtest
from costs import apply_costs
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


def test_plan_entry_fills_at_build_plan_limit(monkeypatch):
    """plan モードのエントリーは build_plan の limit_price で約定する（旧 close-0.5*ATR ではない）。

    evaluate と build_plan を固定し、確実に買いシグナル＋到達可能な提示指値を出して
    「約定価格＝build_plan の limit_price＋スリッページ」を決定論的に検証（設計§5.2 検証=提示）。
    """
    import backtest as bt_mod

    df = _trend_up_df(n=80)
    cost = {"commission_bps": 0.0, "slippage_bps": 10.0}
    proposed: set[float] = set()

    def fake_plan(window, direction, score, configs=None):
        # 当日終値より十分高い指値＝翌日ほぼ確実に約定。旧ロジック close-0.5*ATR とは明確に別値。
        limit = float(window["close"].iloc[-1]) * 1.5
        proposed.add(round(apply_costs(limit, "buy", cost), 6))
        return {"limit_price": limit, "stop_price": limit * 0.5,
                "target_price": limit * 3.0, "atr": 10.0, "rationale": "x"}

    monkeypatch.setattr(bt_mod, "evaluate", lambda *a, **k: (3, "buy", {}))
    monkeypatch.setattr(bt_mod, "build_plan", fake_plan)

    r = bt_mod.run_backtest({"X.T": df}, configs=DEFAULT_CONFIGS, exit_mode="plan",
                            backtest_days=40, cost=cost)
    buys = [t for t in r["trades"] if t["action"] == "buy"]
    assert buys, "買い約定が発生していること"
    # 約定価格は build_plan の提示指値＋スリッページ（旧 close-0.5*ATR なら別値になる）
    for t in buys:
        assert round(t["price"], 6) in proposed


def test_regime_at_is_asof():
    from backtest import _regime_at
    s = pd.Series({pd.Timestamp("2026-01-05"): "risk_on",
                   pd.Timestamp("2026-01-10"): "risk_off"})
    assert _regime_at(s, pd.Timestamp("2026-01-07")) == "risk_on"   # 直近以前
    assert _regime_at(s, pd.Timestamp("2026-01-10")) == "risk_off"
    assert _regime_at(s, pd.Timestamp("2026-01-01")) is None        # 系列開始前 → None
    assert _regime_at(None, pd.Timestamp("2026-01-07")) is None


def test_run_backtest_regime_series_suppresses_risk_off_buys():
    from signals import regime_series
    stock = _trend_up_df(n=120, seed=5)
    idx_close = np.linspace(1300, 1000, 120)            # 同期間の下降指数 → ほぼ risk_off
    index_df = pd.DataFrame({"open": idx_close, "high": idx_close, "low": idx_close,
                             "close": idx_close, "volume": np.full(120, 1e6)},
                            index=stock.index)
    rs = regime_series(index_df)
    without = run_backtest({"X.T": stock}, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60)
    withr = run_backtest({"X.T": stock}, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                         regime_series=rs)
    assert withr["trade_count"] <= without["trade_count"]   # ゲートはBUY抑制のみ＝増えない
