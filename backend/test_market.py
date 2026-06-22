"""market.py のネット非依存テスト（yfinance はスタブする）。"""
import pandas as pd
import pytest

import market


def test_fetch_earnings_dates_normalizes_and_sorts(monkeypatch):
    """tz-aware・降順の earnings index を tz-naive・midnight・昇順のリストに正規化する。"""
    class FakeTicker:
        def __init__(self, t):
            pass
        def get_earnings_dates(self, limit=12):
            idx = pd.DatetimeIndex([
                pd.Timestamp("2026-08-05 16:00", tz="America/New_York"),
                pd.Timestamp("2026-05-07 16:00", tz="America/New_York"),
            ])
            return pd.DataFrame({"EPS Estimate": [1.0, 0.9]}, index=idx)

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)
    out = market.fetch_earnings_dates("X.T")
    assert out == [pd.Timestamp("2026-05-07"), pd.Timestamp("2026-08-05")]


def test_fetch_earnings_dates_handles_missing(monkeypatch):
    """例外・空 DataFrame はどちらも None（best-effort・例外を投げない契約）。"""
    class Boom:
        def __init__(self, t):
            pass
        def get_earnings_dates(self, limit=12):
            raise RuntimeError("no data for JP ticker")

    monkeypatch.setattr("yfinance.Ticker", Boom)
    assert market.fetch_earnings_dates("X.T") is None

    class Empty:
        def __init__(self, t):
            pass
        def get_earnings_dates(self, limit=12):
            return pd.DataFrame()

    monkeypatch.setattr("yfinance.Ticker", Empty)
    assert market.fetch_earnings_dates("X.T") is None
