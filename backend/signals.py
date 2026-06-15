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

# スコア閾値（バックテストで調整する前提・UI/DB から上書き可能）。
# 状態ベース設計（v2）では ma_cross / macd の連続トレンドで ±2 が基準になり、
# そこに逆張りオシレーターの押し目/戻りが乗ると ±3 になる。±2 を既定とする。
BUY_THRESHOLD = 2
SELL_THRESHOLD = -2


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
    """指標列が計算済みの DataFrame の最終行をスコアリングする（状態ベース）。

    設計（v2）: 「クロスした当日だけ」発火するエッジ型では 3 指標同時発火がまず
    起きず、閾値 ±3 にほぼ到達しなかった。そこで各指標を毎営業日の“状態”で評価する：

      - トレンド系（ma_cross / macd）: 並び・符号で常時 ±w を出す連続スコア。
      - 逆張り系（rsi / stoch / bbands）: 売られすぎ→+w / 買われすぎ→-w のゾーン判定。
        → 上昇トレンド中の押し目で +1 が乗り score が +3 に届く、という
          スイングらしい「順張りの押し目買い / 戻り売り」が定常的に成立する。
      - ローソク足パターン: 出現は稀なのでボーナス（エッジ）として加点。

    すべて当日までのデータのみ参照（look-ahead bias なし）。
    """
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
            # 状態: 短期MA > 長期MA を上昇トレンド、< を下降トレンドとして常時評価。
            short, long = p.get("short", 5), p.get("long", 25)
            if len(df) >= long:
                cs = _sma(df["close"], short).iloc[-1]
                cl = _sma(df["close"], long).iloc[-1]
                if not (pd.isna(cs) or pd.isna(cl)):
                    if cs > cl:
                        score += w; detail["ma_cross"] = +w
                    elif cs < cl:
                        score -= w; detail["ma_cross"] = -w

        elif rt == "macd":
            # 状態: ヒストグラム（MACD - シグナル）の符号でモメンタムの方向を評価。
            f, s, sig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
            hist = _val(df, f"MACDh_{f}_{s}_{sig}", -1)
            if hist is None:
                continue
            if hist > 0:
                score += w; detail["macd"] = +w
            elif hist < 0:
                score -= w; detail["macd"] = -w

        elif rt == "bbands":
            # 状態: 終値が下限バンド以下＝売られすぎ→+w、上限バンド以上＝買われすぎ→-w。
            length, std = p.get("length", 20), p.get("std", 2.0)
            lower = f"BBL_{length}_{std}_{std}"
            upper = f"BBU_{length}_{std}_{std}"
            cur_close = _val(df, "close", -1)
            cur_lower = _val(df, lower, -1)
            cur_upper = _val(df, upper, -1)
            if None in (cur_close, cur_lower, cur_upper):
                continue
            if cur_close <= cur_lower:
                score += w; detail["bbands"] = +w
            elif cur_close >= cur_upper:
                score -= w; detail["bbands"] = -w

        elif rt == "stoch":
            # 状態: %K が低位（売られすぎ）→+w、高位（買われすぎ）→-w のゾーン判定。
            k, d = p.get("k", 14), p.get("d", 3)
            ck = _val(df, f"STOCHk_{k}_{d}_3", -1)
            if ck is None:
                continue
            if ck < p.get("low", 20):
                score += w; detail["stoch"] = +w
            elif ck > p.get("high", 80):
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


def evaluate(
    df: pd.DataFrame,
    configs: list[dict[str, Any]] | None = None,
    buy_threshold: int = BUY_THRESHOLD,
    sell_threshold: int = SELL_THRESHOLD,
):
    """df: OHLCV（小文字列・古い順）。最終行についてスコア判定する。

    buy_threshold / sell_threshold は UI から調整可能（DB 保存値を渡す）。
    戻り値: (score, direction, detail)
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    df_ind = add_indicators(df)
    score, detail = _score_indicators(df_ind, configs)
    direction = (
        "buy" if score >= buy_threshold
        else "sell" if score <= sell_threshold
        else "neutral"
    )
    return score, direction, detail
