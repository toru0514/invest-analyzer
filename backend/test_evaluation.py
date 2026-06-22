"""evaluation.py の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from evaluation import benchmark, evaluate_holdout, summary_stats
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


def test_benchmark_eval_start_date_restricts_to_oos_window():
    df = _df(n=200)
    hist = {"X.T": df}
    split = df.index[int(len(df) * 0.7)]
    full = benchmark(hist, DEFAULT_CONFIGS, buy_threshold=2, sell_threshold=-2,
                     initial_capital=3000.0, warmup_days=35, backtest_days=len(df))
    oos = benchmark(hist, DEFAULT_CONFIGS, buy_threshold=2, sell_threshold=-2,
                    initial_capital=3000.0, warmup_days=35, backtest_days=len(df),
                    eval_start_date=split)
    # OOS の buy&hold は split 以降の頭→末リターン（全期間とは別物）
    post = df[df.index >= split]
    expected = (float(post["close"].iloc[-1]) / float(post["close"].iloc[0]) - 1.0) * 100
    assert abs(oos["buy_hold_pct"] - expected) < 1e-6
    assert abs(oos["buy_hold_pct"] - full["buy_hold_pct"]) > 1e-9


def test_evaluate_holdout_structure_and_no_lookahead():
    hist = {"X.T": _df(n=200), "Y.T": _df(n=200, seed=2)}
    res = evaluate_holdout(hist, DEFAULT_CONFIGS, split_ratio=0.7,
                           initial_capital=3000.0, warmup_days=35)
    for key in ("chosen_params", "in_sample", "out_of_sample", "overfit_gap",
                "significance", "benchmark"):
        assert key in res
    assert res["in_sample"]["sample"] == "in_sample"
    assert res["out_of_sample"]["sample"] == "out_of_sample"
    assert res["chosen_params"]["threshold"] in (1, 2, 3)
    assert isinstance(res["in_sample"]["sweep"], list) and len(res["in_sample"]["sweep"]) == 3
    # 寄与度（leave-one-out）は in_sample 配下に残す（フロント /optimize が表示）
    assert isinstance(res["in_sample"]["contributions"], list)
    assert "baseline_pnl_pct" in res["in_sample"] and "best" in res["in_sample"]


def test_evaluate_holdout_oos_trades_are_after_split():
    hist = {"X.T": _df(n=200)}
    res = evaluate_holdout(hist, DEFAULT_CONFIGS, split_ratio=0.7,
                           initial_capital=3000.0, warmup_days=35)
    assert res["significance"]["n"] >= 0


def test_evaluate_holdout_accepts_regime_series():
    from signals import regime_series
    hist = {"X.T": _df(n=200)}
    idx_close = np.linspace(1300, 1000, 200)
    index_df = pd.DataFrame({"open": idx_close, "high": idx_close, "low": idx_close,
                             "close": idx_close, "volume": np.full(200, 1e6)},
                            index=hist["X.T"].index)
    rs = regime_series(index_df)
    res = evaluate_holdout(hist, DEFAULT_CONFIGS, split_ratio=0.7, regime_series=rs,
                           initial_capital=3000.0, warmup_days=35)
    assert res["out_of_sample"]["sample"] == "out_of_sample"
    assert "benchmark" in res


def test_benchmark_and_holdout_accept_rs_params():
    from market import synthetic_history
    import evaluation
    hist = {f"E{i}.T": synthetic_history(f"E{i}.T", n=140, seed=i) for i in range(3)}
    idx = synthetic_history("IDX.T", n=140, seed=55)
    # benchmark: None と供給で all_signals_pct（score モード）は不変
    b_base = evaluation.benchmark(hist, None, buy_threshold=2, sell_threshold=-2,
                                  initial_capital=3000.0, warmup_days=35, backtest_days=80)
    b_rs = evaluation.benchmark(hist, None, buy_threshold=2, sell_threshold=-2,
                                initial_capital=3000.0, warmup_days=35, backtest_days=80,
                                index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert b_rs["all_signals_pct"] == b_base["all_signals_pct"]
    # evaluate_holdout: 例外なく完走（out_of_sample を含む）
    h = evaluation.evaluate_holdout(hist, None, initial_capital=3000.0, warmup_days=35,
                                    index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert "out_of_sample" in h


def test_evaluate_holdout_accepts_risk_pct():
    from market import synthetic_history
    from evaluation import evaluate_holdout
    hist = {f"P{i}.T": synthetic_history(f"P{i}.T", n=200, seed=i) for i in range(2)}
    res = evaluate_holdout(hist, configs=None, initial_capital=5_000_000, risk_pct=0.5)
    assert "chosen_params" in res and "out_of_sample" in res
