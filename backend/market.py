"""データ取得層。

yfinance で日足を取得し、小文字 OHLCV・古い順の DataFrame に正規化する。
ネットワーク制限のある環境向けに、合成データのフォールバックも持つ。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def fetch_history(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """yfinance で日足を取得。失敗時は空 DataFrame。"""
    import yfinance as yf

    raw = yf.download(ticker, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.lower)
    df = raw[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def synthetic_history(ticker: str, n: int = 120, seed: int | None = None) -> pd.DataFrame:
    """ネットワーク不要の合成 OHLCV（ロジック検証 / デモ用）。"""
    rng = np.random.default_rng(seed if seed is not None else abs(hash(ticker)) % (2**32))
    base = 1000 + rng.integers(0, 1500)
    t = np.arange(n)
    drift = np.cumsum(rng.normal(0, 1, n)) * 8
    wave = np.sin(t / 6.0) * 40
    close = np.maximum(base + drift + wave, 50)
    open_ = close + rng.normal(0, 5, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 6, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 6, n))
    vol = rng.integers(1_000_000, 8_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def get_history(ticker: str, period: str = "6mo", demo: bool = False) -> pd.DataFrame:
    """demo=True なら合成データ、そうでなければ yfinance。yfinance 失敗時も空を返す。"""
    if demo:
        return synthetic_history(ticker)
    try:
        return fetch_history(ticker, period=period)
    except Exception:
        return pd.DataFrame()


def fetch_name(ticker: str) -> str | None:
    """yfinance から銘柄名を取得（取れなければ None）。内蔵マスタに無い銘柄の補完用。"""
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        for key in ("longName", "shortName", "displayName"):
            v = info.get(key)
            if v:
                return str(v)
    except Exception:
        pass
    return None


def fetch_earnings_days(ticker: str) -> int | None:
    """直近の将来決算日までの日数（best-effort）。取得不可・無しは None。

    yfinance の決算日は日本株では欠落しがち。例外は握りつぶして None を返す。
    """
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=12)
        if df is None or df.empty:
            return None
        idx = pd.to_datetime(df.index)
        now = pd.Timestamp.now(tz=idx.tz)
        future = idx[idx >= now]
        if len(future) == 0:
            return None
        return int((future.min().normalize() - now.normalize()).days)
    except Exception:
        return None


def fetch_earnings_dates(ticker: str, limit: int = 12) -> list[pd.Timestamp] | None:
    """過去＋将来の決算日（tz-naive・midnight・昇順）。取得不可・無しは None（best-effort）。

    yfinance の決算日 index は tz-aware（取引所TZ）なことが多い。tz を落として
    日付化し、バックテストの df.index（tz-naive・get_history 由来）と素直に比較できるようにする。
    例外・空は None（fetch_earnings_days と同じ堅牢契約・例外は投げない）。
    """
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if df is None or df.empty:
            return None
        idx = pd.to_datetime(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        idx = idx.dropna().normalize()   # NaT 行（将来決算で日付未確定など）を落とし、確定分は残す
        dates = sorted(set(idx))
        return dates or None
    except Exception:
        return None
