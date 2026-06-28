"""データ健全性・流動性の純関数（DB/ネット非依存）。打ち手12。

average_turnover: 直近 window バーの平均売買代金（close×volume・円）。
data_health: 直近 window バーの {zero_volume_days, gap_days, spike_days}。
いずれも不正入力・空・列欠落・nan を安全な既定で返し、例外を投げない
（market の best-effort 契約・signals.volume_ratio の None 返しと同方針）。
"""
from __future__ import annotations

import numpy as np

from holidays_jp import MARKET_HOLIDAYS

# 連続欠損のしきい値: 祝日対応の取引日距離 >= 2（＝1取引日以上の欠損）。
_GAP_BDAYS = 2


def average_turnover(df, window: int = 20) -> float | None:
    """平均売買代金（円・直近 window バーの close×volume の平均）。

    列欠落 / len(df) < window / nan は None（算出不可＝不明）。例外を投げない。
    """
    try:
        if df is None or "close" not in df.columns or "volume" not in df.columns:
            return None
        if len(df) < window:
            return None
        turnover = (df["close"].astype(float) * df["volume"].astype(float)).tail(window).mean()
        if turnover != turnover:        # nan
            return None
        return float(turnover)
    except Exception:
        return None
