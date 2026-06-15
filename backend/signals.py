"""シグナル計算コア（Phase 0）

複数のテクニカル指標・ローソク足パターンを計算し、重み付きスコアで
buy / sell / neutral を判定する。look-ahead bias を避けるため、判定には
その営業日までのデータのみを使う（pandas-ta の指標はすべて因果的＝過去と
当日のみ参照するため、全期間で一括計算したものをスライスして読んでも結果は同じ）。

Phase 1 以降（FastAPI / SQLite）でもこのモジュールをそのまま再利用する。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pandas_ta as ta  # noqa: F401  (DataFrame.ta アクセサを有効化するために必要)


# ---------------------------------------------------------------------------
# シグナル条件のデフォルト設定（signal_config テーブルに相当）
# まずは「どの技が効くか未知」なので全部 enabled / weight=1 で積む。
# バックテストの結果を見て weight や閾値を調整していく前提。
# ---------------------------------------------------------------------------
DEFAULT_CONFIGS: list[dict[str, Any]] = [
    {"rule_type": "rsi", "params": {"length": 14, "low": 30, "high": 70}, "weight": 1, "enabled": 1},
    {"rule_type": "ma_cross", "params": {"short": 5, "long": 25}, "weight": 1, "enabled": 1},
    {"rule_type": "macd", "params": {"fast": 12, "slow": 26, "signal": 9}, "weight": 1, "enabled": 1},
    {"rule_type": "bbands", "params": {"length": 20, "std": 2.0}, "weight": 1, "enabled": 1},
    {"rule_type": "stoch", "params": {"k": 14, "d": 3, "low": 20, "high": 80}, "weight": 1, "enabled": 1},
    {"rule_type": "candle_pattern", "params": {}, "weight": 1, "enabled": 1},
    # price_target はスコアと独立した「即通知」経路。バックテストのスコアには算入しない。
    # {"rule_type": "price_target", "params": {"above": 1500}, "weight": 1, "enabled": 1},
]

# スコア閾値（バックテストで調整する前提）
BUY_THRESHOLD = 3
SELL_THRESHOLD = -3


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV DataFrame（小文字列・古い順）に指標列を追加して返す。"""
    df = df.copy()
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2.0, append=True)
    df.ta.stoch(k=14, d=3, append=True)
    # ローソク足パターン（TA-Lib 必須）。赤三兵 / 三羽烏 / 包み足。
    patterns = df.ta.cdl_pattern(name=["3whitesoldiers", "3blackcrows", "engulfing"])
    for col in patterns.columns:
        df[col] = patterns[col]
    return df


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


def golden_cross(df: pd.DataFrame, short: int, long: int) -> bool:
    """短期MAが長期MAを当日上抜けしたか（前日は短<=長、当日は短>長）。"""
    if len(df) < long + 1:
        return False
    s = _sma(df["close"], short)
    l = _sma(df["close"], long)
    prev_s, prev_l = s.iloc[-2], l.iloc[-2]
    cur_s, cur_l = s.iloc[-1], l.iloc[-1]
    if pd.isna(prev_s) or pd.isna(prev_l) or pd.isna(cur_s) or pd.isna(cur_l):
        return False
    return prev_s <= prev_l and cur_s > cur_l


def dead_cross(df: pd.DataFrame, short: int, long: int) -> bool:
    """短期MAが長期MAを当日下抜けしたか（前日は短>=長、当日は短<長）。"""
    if len(df) < long + 1:
        return False
    s = _sma(df["close"], short)
    l = _sma(df["close"], long)
    prev_s, prev_l = s.iloc[-2], l.iloc[-2]
    cur_s, cur_l = s.iloc[-1], l.iloc[-1]
    if pd.isna(prev_s) or pd.isna(prev_l) or pd.isna(cur_s) or pd.isna(cur_l):
        return False
    return prev_s >= prev_l and cur_s < cur_l


def _val(df: pd.DataFrame, col: str, i: int = -1):
    """列が存在し NaN でなければ値を返し、なければ None。"""
    if col not in df.columns:
        return None
    v = df[col].iloc[i]
    return None if pd.isna(v) else v


def _score_indicators(df: pd.DataFrame, configs: list[dict[str, Any]]) -> tuple[int, dict]:
    """指標列が計算済みの DataFrame の最終行をスコアリングする。"""
    score, detail = 0, {}
    for cfg in configs:
        if not cfg.get("enabled", 1):
            continue
        p = cfg.get("params") or {}
        if isinstance(p, str):
            p = json.loads(p or "{}")
        rt = cfg["rule_type"]
        w = int(cfg.get("weight", 1))

        if rt == "rsi":
            rsi = _val(df, f"RSI_{p.get('length', 14)}")
            if rsi is None:
                continue
            if rsi < p.get("low", 30):
                score += w; detail["rsi"] = +w
            elif rsi > p.get("high", 70):
                score -= w; detail["rsi"] = -w

        elif rt == "ma_cross":
            short, long = p.get("short", 5), p.get("long", 25)
            if golden_cross(df, short, long):
                score += w; detail["ma_cross"] = +w
            elif dead_cross(df, short, long):
                score -= w; detail["ma_cross"] = -w

        elif rt == "macd":
            f, s, sig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
            hist_col = f"MACDh_{f}_{s}_{sig}"
            cur = _val(df, hist_col, -1)
            prev = _val(df, hist_col, -2) if len(df) >= 2 else None
            if cur is None or prev is None:
                continue
            if prev <= 0 < cur:        # ヒストグラムが負→正：シグナル上抜け
                score += w; detail["macd"] = +w
            elif prev >= 0 > cur:      # 正→負：シグナル下抜け
                score -= w; detail["macd"] = -w

        elif rt == "bbands":
            length, std = p.get("length", 20), p.get("std", 2.0)
            lower = f"BBL_{length}_{std}_{std}"
            upper = f"BBU_{length}_{std}_{std}"
            cur_close = _val(df, "close", -1)
            prev_close = _val(df, "close", -2) if len(df) >= 2 else None
            prev_lower = _val(df, lower, -2) if len(df) >= 2 else None
            prev_upper = _val(df, upper, -2) if len(df) >= 2 else None
            if None in (cur_close, prev_close, prev_lower, prev_upper):
                continue
            if prev_close <= prev_lower and cur_close > prev_close:   # 下限タッチ後の反発
                score += w; detail["bbands"] = +w
            elif prev_close >= prev_upper and cur_close < prev_close:  # 上限タッチ後の反落
                score -= w; detail["bbands"] = -w

        elif rt == "stoch":
            k, d = p.get("k", 14), p.get("d", 3)
            kcol, dcol = f"STOCHk_{k}_{d}_3", f"STOCHd_{k}_{d}_3"
            ck, cd = _val(df, kcol, -1), _val(df, dcol, -1)
            pk, pd_ = (_val(df, kcol, -2), _val(df, dcol, -2)) if len(df) >= 2 else (None, None)
            if None in (ck, cd, pk, pd_):
                continue
            if pk <= pd_ and ck > cd and ck < p.get("low", 20):    # 低位での %K 上抜け
                score += w; detail["stoch"] = +w
            elif pk >= pd_ and ck < cd and ck > p.get("high", 80):  # 高位での %K 下抜け
                score -= w; detail["stoch"] = -w

        elif rt == "candle_pattern":
            if (_val(df, "CDL_3WHITESOLDIERS") or 0) > 0:   # 赤三兵
                score += w; detail["3whitesoldiers"] = +w
            if (_val(df, "CDL_3BLACKCROWS") or 0) < 0:      # 三羽烏
                score -= w; detail["3blackcrows"] = -w
            eng = _val(df, "CDL_ENGULFING") or 0            # 包み足
            if eng > 0:
                score += w; detail["engulfing"] = +w
            elif eng < 0:
                score -= w; detail["engulfing"] = -w

        elif rt == "price_target":
            # スコアとは別経路（即通知）。バックテストのスコアには算入しない。
            continue

    return score, detail


def evaluate(df: pd.DataFrame, configs: list[dict[str, Any]] | None = None):
    """df: OHLCV（小文字列・古い順）。最終行についてスコア判定する。

    戻り値: (score, direction, detail)
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    df_ind = add_indicators(df)
    score, detail = _score_indicators(df_ind, configs)
    direction = "buy" if score >= BUY_THRESHOLD else "sell" if score <= SELL_THRESHOLD else "neutral"
    return score, direction, detail
