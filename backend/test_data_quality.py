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
