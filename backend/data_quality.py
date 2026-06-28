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


def data_health(df, window: int = 20, spike_pct: float = 0.5) -> dict:
    """直近 window バーのデータ健全性カウント。

    戻り: {"zero_volume_days", "gap_days", "spike_days"}（すべて 0 以上の int）。
    不正入力・空・列欠落は全 0。例外を投げない。
    """
    zero = {"zero_volume_days": 0, "gap_days": 0, "spike_days": 0}
    try:
        if df is None or len(df) == 0 or "close" not in df.columns:
            return dict(zero)
        recent = df.tail(window + 1)        # +1: spike のリターン計算に1本余分
        out = dict(zero)

        # 出来高0日（直近 window 本）
        if "volume" in recent.columns:
            vol = recent["volume"].tail(window).astype(float)
            out["zero_volume_days"] = int(((vol <= 0) | vol.isna()).sum())

        # 異常スパイク（|日次変化率| > spike_pct）
        rets = recent["close"].astype(float).pct_change().dropna()
        out["spike_days"] = int((rets.abs() > spike_pct).sum())

        # 連続欠損（祝日対応の取引日距離 >= _GAP_BDAYS）
        dates = recent.index[-window:]
        if len(dates) >= 2:
            cal = np.busdaycalendar(holidays=sorted(MARKET_HOLIDAYS))
            d = dates.normalize().values.astype("datetime64[D]")   # np.busday_count は datetime64[D] 必須
            gaps = np.busday_count(d[:-1], d[1:], busdaycal=cal)
            out["gap_days"] = int((gaps >= _GAP_BDAYS).sum())

        return out
    except Exception:
        return dict(zero)
