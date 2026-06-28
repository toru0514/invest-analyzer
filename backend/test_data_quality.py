"""data_quality.py のネット/DB非依存テスト（統制 OHLC で決定論化）。打ち手12。"""
import numpy as np
import pandas as pd

import data_quality as dq
import holidays_jp


def _trading_days(n: int, end: str = "2026-06-26") -> pd.DatetimeIndex:
    """末尾 n 本の東証営業日（祝日を除く）。連続営業日の busday_count が 1 になるように。"""
    days = [d for d in pd.bdate_range(end=end, periods=n + 40)
            if d.strftime("%Y-%m-%d") not in holidays_jp.MARKET_HOLIDAYS]
    return pd.DatetimeIndex(days[-n:])


def _df(closes, volumes, dates=None) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    if dates is None:
        dates = _trading_days(len(closes))
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
        index=pd.DatetimeIndex(dates),
    )


def test_average_turnover_basic():
    df = _df([100.0] * 20, [10_000] * 20)
    assert dq.average_turnover(df, window=20) == 1_000_000.0


def test_average_turnover_insufficient_returns_none():
    df = _df([100.0] * 10, [10_000] * 10)
    assert dq.average_turnover(df, window=20) is None


def test_average_turnover_missing_column_returns_none():
    df = _df([100.0] * 20, [10_000] * 20).drop(columns=["volume"])
    assert dq.average_turnover(df, window=20) is None


def test_average_turnover_all_zero_volume_is_zero():
    df = _df([100.0] * 20, [0] * 20)
    assert dq.average_turnover(df, window=20) == 0.0


def test_average_turnover_garbage_returns_none():
    assert dq.average_turnover(None) is None
    assert dq.average_turnover(pd.DataFrame()) is None


_ZERO = {"zero_volume_days": 0, "gap_days": 0, "spike_days": 0}


def test_data_health_clean():
    df = _df([100.0 + i for i in range(25)], [1_000_000] * 25)
    assert dq.data_health(df) == _ZERO


def test_data_health_zero_volume_days():
    vols = [1_000_000] * 25
    vols[-1] = 0
    vols[-3] = 0
    df = _df([100.0 + i * 0.1 for i in range(25)], vols)
    assert dq.data_health(df)["zero_volume_days"] == 2


def test_data_health_spike_days():
    closes = [100.0] * 25
    closes[-2] = 300.0   # +200% → 翌日 -66.7%。どちらも |変化|>50% ＝ 2本
    df = _df(closes, [1_000_000] * 25)
    assert dq.data_health(df)["spike_days"] == 2


def test_data_health_no_spike_below_threshold():
    closes = [100.0] * 25
    closes[-2] = 120.0   # +20% / -16.7% ＝ どちらも <50%
    df = _df(closes, [1_000_000] * 25)
    assert dq.data_health(df)["spike_days"] == 0


def test_data_health_gap_detected():
    dates = list(_trading_days(25))
    del dates[-3]        # 取引日を1本抜く＝その前後で取引日距離 2 のギャップ
    df = _df([100.0 + i * 0.1 for i in range(24)], [1_000_000] * 24, dates=dates)
    assert dq.data_health(df, window=24)["gap_days"] >= 1


def test_data_health_holiday_not_flagged_as_gap():
    """2026 GW（4/29・5/3-5/6 休場）を跨ぐ連続営業日は欠損ではない。"""
    biz = _trading_days(20, end="2026-05-15")   # GW を含む直近営業日
    df = _df([100.0 + i * 0.1 for i in range(20)], [1_000_000] * 20, dates=biz)
    assert dq.data_health(df, window=20)["gap_days"] == 0


def test_data_health_garbage_returns_zero():
    assert dq.data_health(None) == _ZERO
    assert dq.data_health(pd.DataFrame()) == _ZERO
    assert dq.data_health(_df([100.0] * 5, [1_000_000] * 5).drop(columns=["close"])) == _ZERO
