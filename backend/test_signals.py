"""ネットワーク不要のスモークテスト。

    backend/venv/bin/python -m pytest backend/test_signals.py
  もしくは
    backend/venv/bin/python backend/test_signals.py
"""

from __future__ import annotations

import signals
from backtest import INITIAL_CAPITAL, run_backtest
from market import synthetic_history


def test_evaluate_returns_valid_direction():
    df = synthetic_history("TEST.T", n=120, seed=42)
    score, direction, detail = signals.evaluate(df)
    assert isinstance(score, int)
    assert direction in ("buy", "sell", "neutral")
    assert isinstance(detail, dict)


def test_golden_dead_cross_are_exclusive():
    df = synthetic_history("TEST.T", n=120, seed=1)
    g = signals.golden_cross(df, 5, 25)
    d = signals.dead_cross(df, 5, 25)
    assert not (g and d)


def test_backtest_reports_all_required_metrics():
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T"])}
    r = run_backtest(hist)
    for key in ("initial", "final", "pnl_amount", "pnl_pct",
                "trade_count", "win_rate", "max_drawdown_pct"):
        assert key in r
    assert r["initial"] == INITIAL_CAPITAL
    assert r["trade_count"] >= 0
    assert r["max_drawdown_pct"] >= 0


def test_trades_execute_when_thresholds_low():
    # 閾値を緩めれば（±1）売買が成立することを確認する。
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist, buy_threshold=1, sell_threshold=-1)
    assert r["trade_count"] > 0
    assert r["final"] > 0


def _base_configs():
    # 追補版の補正フィルター（出来高/週足/ATR）を除いた、状態ベースの6指標のみ。
    return [c for c in signals.DEFAULT_CONFIGS
            if c["rule_type"] not in ("volume_filter", "weekly_trend_filter", "atr_exit")]


def test_state_based_scoring_reaches_default_threshold():
    # 状態ベース設計では既定閾値（±2）で売買が成立するはず（v2 の要）。
    # 出来高/週足フィルターは別途検証するため、ここでは基本6指標で評価する。
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist, configs=_base_configs())
    assert r["trade_count"] > 0


def test_volume_ratio_and_filter_bonus():
    import numpy as np
    import pandas as pd
    df = synthetic_history("TEST.T", n=120, seed=3)
    # 当日出来高を平均比で大きく/小さくして vol_ratio を確認
    df.loc[df.index[-1], "volume"] = df["volume"].iloc[-30:-1].mean() * 3
    vr = signals.volume_ratio(df, sma=20)
    assert vr is not None and vr > 1.5

    # score!=0 のとき surge でボーナスが付く（同方向に増える）
    cfg_no_vol = _base_configs()
    cfg_with_vol = _base_configs() + [
        {"rule_type": "volume_filter",
         "params": {"sma": 20, "surge": 1.5, "quiet": 0.7, "bonus": 1}, "weight": 1, "enabled": 1}]
    s0, _, _ = signals.evaluate(df, cfg_no_vol)
    s1, _, det = signals.evaluate(df, cfg_with_vol)
    if s0 > 0:
        assert s1 >= s0 and det.get("volume") == 1
    elif s0 < 0:
        assert s1 <= s0


def test_weekly_trend_block_suppresses_counter_trend():
    import numpy as np
    import pandas as pd
    # 明確な下降トレンドの日足を作る → 週足 down
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-15"), periods=160)
    close = np.linspace(3000, 1500, len(idx))
    df = pd.DataFrame({"open": close, "high": close + 5, "low": close - 5,
                       "close": close, "volume": 1_000_000.0}, index=idx)
    assert signals.weekly_trend(df) == "down"

    # block モードでは buy 方向（仮に出ても）が neutral に丸められる方向に働く
    cfg = _base_configs() + [
        {"rule_type": "weekly_trend_filter", "params": {"sma": 13, "mode": "block"},
         "weight": 1, "enabled": 1}]
    _, direction, det = signals.evaluate(df, cfg)
    assert direction in ("sell", "neutral")  # 下降中に buy は出さない


def test_build_plan_exits_are_ordered():
    df = synthetic_history("TEST.T", n=120, seed=7)
    close = float(df["close"].iloc[-1])
    buy = signals.build_plan(df, "buy", 3)
    assert buy["atr"] is not None
    assert buy["stop_price"] < close < buy["target_price"]
    sell = signals.build_plan(df, "sell", -3)
    assert sell["target_price"] < close < sell["stop_price"]


if __name__ == "__main__":
    test_evaluate_returns_valid_direction()
    test_golden_dead_cross_are_exclusive()
    test_backtest_reports_all_required_metrics()
    test_trades_execute_when_thresholds_low()
    test_state_based_scoring_reaches_default_threshold()
    test_volume_ratio_and_filter_bonus()
    test_weekly_trend_block_suppresses_counter_trend()
    test_build_plan_exits_are_ordered()
    print("all smoke tests passed")
