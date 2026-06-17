"""evaluation.py の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

from evaluation import summary_stats


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
