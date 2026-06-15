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


def test_state_based_scoring_reaches_default_threshold():
    # 状態ベース設計では既定閾値（±2）で売買が成立するはず（v2 の要）。
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist)
    assert r["trade_count"] > 0


if __name__ == "__main__":
    test_evaluate_returns_valid_direction()
    test_golden_dead_cross_are_exclusive()
    test_backtest_reports_all_required_metrics()
    test_trades_execute_when_thresholds_low()
    test_state_based_scoring_reaches_default_threshold()
    print("all smoke tests passed")
