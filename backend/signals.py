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
    # 乖離率（移動平均からの乖離%）。MA より大きく下なら売られすぎ→買い、上なら買われすぎ→売り。
    {"rule_type": "disparity", "params": {"ma": 25, "low": -7, "high": 7}, "weight": 1, "enabled": 1},
    # OBV（出来高系）: OBV がその移動平均より上＝出来高が上昇を支持→買い、下→売り。
    {"rule_type": "obv", "params": {"sma": 20}, "weight": 1, "enabled": 1},
    # CCI（逆張りオシレーター）: -100 以下で売られすぎ→買い、+100 以上で買われすぎ→売り。
    {"rule_type": "cci", "params": {"length": 20, "low": -100, "high": 100}, "weight": 1, "enabled": 1},
    # 追補版の強化（B/C/D）。スコアを多面的に補正する。
    {"rule_type": "volume_filter", "params": {"sma": 20, "surge": 1.5, "quiet": 0.7, "bonus": 1}, "weight": 1, "enabled": 1},
    {"rule_type": "weekly_trend_filter", "params": {"sma": 13, "mode": "penalty"}, "weight": 1, "enabled": 1},
    # 地合いレジーム（指数版の一次ゲート）: risk_off の買いを penalty/block で抑制する。
    {"rule_type": "market_regime",
     "params": {"mode": "penalty", "penalty": 2, "sma": 13, "dd_lookback": 60, "dd_threshold": 0.10},
     "weight": 1, "enabled": 1},
    {"rule_type": "atr_exit", "params": {"length": 14, "stop_mult": 1.5, "target_mult": 1.5, "limit_method": "ma", "limit_ma": 5, "entry_atr_mult": 0.5, "support_n": 20}, "weight": 1, "enabled": 1},
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


def resolve_configs(common: list[dict[str, Any]],
                    ticker_specific: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """銘柄固有設定で共通設定を rule_type 単位で上書きした実効リストを返す。

    例: ある銘柄に atr_exit を1つ登録すると、その銘柄ではその出口設定が共通より優先される。
    price_target は1銘柄に複数登録できる即通知ルールなので上書き対象から除外し、すべて残す。
    """
    override_rules = {c["rule_type"] for c in ticker_specific if c["rule_type"] != "price_target"}
    result = [c for c in common if c["rule_type"] not in override_rules]
    result += ticker_specific
    return result


def _find_cfg(configs: list[dict[str, Any]], rule_type: str) -> dict | None:
    """有効な指定 rule_type の params を返す（無ければ None）。"""
    for c in configs:
        if c.get("rule_type") == rule_type and c.get("enabled", 1):
            p = c.get("params") or {}
            if isinstance(p, str):
                p = json.loads(p or "{}")
            return p
    return None


# ---------------------------------------------------------------------------
# 追補版の強化で使う計算（すべて当日までのデータのみ参照＝look-ahead bias なし）
# ---------------------------------------------------------------------------
def volume_ratio(df: pd.DataFrame, sma: int = 20) -> float | None:
    """当日出来高 ÷ 出来高移動平均（強化1）。"""
    if "volume" not in df.columns or len(df) < sma:
        return None
    vsma = df["volume"].rolling(sma).mean().iloc[-1]
    last = df["volume"].iloc[-1]
    if pd.isna(vsma) or vsma <= 0 or pd.isna(last):
        return None
    return float(last) / float(vsma)


def weekly_trend(df: pd.DataFrame, sma: int = 13, lookback: int = 4,
                 flat_eps: float = 0.002) -> str:
    """日足を週足にリサンプルし、週足 SMA の傾きで 'up'/'down'/'flat' を返す（強化2）。

    別途 yfinance で週足を取得する代わりに、同じ日足データから週足を作る。
    これにより各営業日の判定はその日までのデータのみで完結し、look-ahead bias を避けられる。
    """
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 5:
        return "flat"
    weekly_close = df["close"].resample("W").last().dropna()
    if len(weekly_close) < sma + lookback:
        return "flat"
    s = weekly_close.rolling(sma).mean()
    cur, prev = s.iloc[-1], s.iloc[-1 - lookback]
    if pd.isna(cur) or pd.isna(prev) or prev == 0:
        return "flat"
    chg = (cur - prev) / abs(prev)
    if chg > flat_eps:
        return "up"
    if chg < -flat_eps:
        return "down"
    return "flat"


def market_regime(index_df, *, sma: int = 13, dd_lookback: int = 60,
                  dd_threshold: float = 0.10) -> str:
    """指数 OHLCV の最終行時点の地合いレジームを返す: 'risk_on'|'neutral'|'risk_off'。

    呼び出し側が index_df を日付で切ることで look-ahead を回避する。
    """
    if index_df is None or len(index_df) < 5:
        return "neutral"
    trend = weekly_trend(index_df, sma)
    closes = index_df["close"].tail(dd_lookback)
    peak = float(closes.max())
    last = float(closes.iloc[-1])
    dd = (peak - last) / peak if peak > 0 else 0.0
    if trend == "down" or dd >= dd_threshold:
        return "risk_off"
    if trend == "up" and dd < dd_threshold / 2:
        return "risk_on"
    return "neutral"


def regime_series(index_df, **params) -> "pd.Series":
    """各営業日について「その日までの指数」でのレジームを前計算（look-ahead 安全）。"""
    idx = index_df.sort_index()
    return pd.Series({idx.index[i]: market_regime(idx.iloc[:i + 1], **params)
                      for i in range(len(idx))})


def atr_value(df: pd.DataFrame, length: int = 14) -> float | None:
    """ATR（Average True Range・期間 length）の最新値（強化3）。"""
    if len(df) < length + 1:
        return None
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(length).mean().iloc[-1]
    return None if pd.isna(atr) else float(atr)


def obv_vs_sma(df: pd.DataFrame, sma: int = 20):
    """(OBV最新値, OBVのSMA最新値) を返す。出来高で上昇/下降を確認する（出来高系）。"""
    if "volume" not in df.columns or len(df) < sma + 1:
        return None, None
    direction = df["close"].diff().fillna(0.0)
    signed = df["volume"].where(direction >= 0, -df["volume"])
    obv = signed.cumsum()
    obv_sma = obv.rolling(sma).mean().iloc[-1]
    cur = obv.iloc[-1]
    if pd.isna(obv_sma) or pd.isna(cur):
        return None, None
    return float(cur), float(obv_sma)


def cci_value(df: pd.DataFrame, length: int = 20) -> float | None:
    """CCI（Commodity Channel Index）の最新値のみを効率的に算出（逆張りオシレーター）。"""
    if len(df) < length:
        return None
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    window = tp.iloc[-length:]
    mean = window.mean()
    mad = (window - mean).abs().mean()
    if mad == 0 or pd.isna(mad):
        return None
    return float((tp.iloc[-1] - mean) / (0.015 * mad))


# 相関でグルーピング（打ち手4）。グループ内は合算→±GROUP_CAP にクリップして多重カウントを止める。
INDICATOR_GROUP = {
    "ma_cross": "trend", "macd": "trend",
    "rsi": "contrarian", "bbands": "contrarian", "stoch": "contrarian",
    "disparity": "contrarian", "cci": "contrarian",
    "obv": "volume",
    "candle_pattern": "pattern",
}
GROUP_CAP = 1   # グループ内の最大寄与（±）


def _score_indicators(df: pd.DataFrame, configs: list[dict[str, Any]]) -> tuple[int, dict]:
    """指標列が計算済みの DataFrame の最終行をスコアリング（状態ベース・グループ化）。

    各指標は ±weight を出すが、相関グループ（順張り/逆張り/需給/パターン）ごとに合算して
    ±GROUP_CAP にクリップしてから合算する。これにより逆張り系5指標の多重カウントを止める。
    すべて当日までのデータのみ参照（look-ahead bias なし）。
    """
    group_raw: dict[str, int] = {}
    detail: dict = {}

    def _add(rt: str, key: str, v: int):
        detail[key] = v
        g = INDICATOR_GROUP.get(rt, rt)
        group_raw[g] = group_raw.get(g, 0) + v

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
                _add(rt, "rsi", +w)
            elif rsi > p.get("high", 70):
                _add(rt, "rsi", -w)

        elif rt == "ma_cross":
            short, long = p.get("short", 5), p.get("long", 25)
            if len(df) >= long:
                cs = _sma(df["close"], short).iloc[-1]
                cl = _sma(df["close"], long).iloc[-1]
                if not (pd.isna(cs) or pd.isna(cl)):
                    if cs > cl:
                        _add(rt, "ma_cross", +w)
                    elif cs < cl:
                        _add(rt, "ma_cross", -w)

        elif rt == "macd":
            f, s, sig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
            hist = _val(df, f"MACDh_{f}_{s}_{sig}", -1)
            if hist is None:
                continue
            if hist > 0:
                _add(rt, "macd", +w)
            elif hist < 0:
                _add(rt, "macd", -w)

        elif rt == "bbands":
            length, std = p.get("length", 20), p.get("std", 2.0)
            lower = f"BBL_{length}_{std}_{std}"
            upper = f"BBU_{length}_{std}_{std}"
            cur_close = _val(df, "close", -1)
            cur_lower = _val(df, lower, -1)
            cur_upper = _val(df, upper, -1)
            if None in (cur_close, cur_lower, cur_upper):
                continue
            if cur_close <= cur_lower:
                _add(rt, "bbands", +w)
            elif cur_close >= cur_upper:
                _add(rt, "bbands", -w)

        elif rt == "stoch":
            k, d = p.get("k", 14), p.get("d", 3)
            ck = _val(df, f"STOCHk_{k}_{d}_3", -1)
            if ck is None:
                continue
            if ck < p.get("low", 20):
                _add(rt, "stoch", +w)
            elif ck > p.get("high", 80):
                _add(rt, "stoch", -w)

        elif rt == "candle_pattern":
            if (_val(df, "CDL_3WHITESOLDIERS") or 0) > 0:
                _add(rt, "3whitesoldiers", +w)
            if (_val(df, "CDL_3BLACKCROWS") or 0) < 0:
                _add(rt, "3blackcrows", -w)
            eng = _val(df, "CDL_ENGULFING") or 0
            if eng > 0:
                _add(rt, "engulfing", +w)
            elif eng < 0:
                _add(rt, "engulfing", -w)

        elif rt == "disparity":
            ma_len = int(p.get("ma", 25))
            ma = _sma(df["close"], ma_len).iloc[-1]
            cur_close = _val(df, "close", -1)
            if pd.isna(ma) or ma == 0 or cur_close is None:
                continue
            disp = (cur_close - ma) / ma * 100
            if disp <= p.get("low", -7):
                _add(rt, "disparity", +w)
            elif disp >= p.get("high", 7):
                _add(rt, "disparity", -w)

        elif rt == "obv":
            obv, obv_sma = obv_vs_sma(df, int(p.get("sma", 20)))
            if obv is None:
                continue
            if obv > obv_sma:
                _add(rt, "obv", +w)
            elif obv < obv_sma:
                _add(rt, "obv", -w)

        elif rt == "cci":
            cci = cci_value(df, int(p.get("length", 20)))
            if cci is None:
                continue
            if cci <= p.get("low", -100):
                _add(rt, "cci", +w)
            elif cci >= p.get("high", 100):
                _add(rt, "cci", -w)

        elif rt == "price_target":
            continue   # スコア対象外（即通知の別経路）

    groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
    detail["_groups"] = groups
    score = sum(groups.values())
    return score, detail


def evaluate(
    df: pd.DataFrame,
    configs: list[dict[str, Any]] | None = None,
    buy_threshold: int = BUY_THRESHOLD,
    sell_threshold: int = SELL_THRESHOLD,
    regime: str | None = None,
):
    """df: OHLCV（小文字列・古い順）。最終行についてスコア判定する。

    buy_threshold / sell_threshold は UI から調整可能（DB 保存値を渡す）。
    戻り値: (score, direction, detail)。detail には各指標の個別寄与に加え、
    detail["_groups"]（順張り/逆張り/需給/パターンのグループ別純額・打ち手4）が入る。
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    df_ind = add_indicators(df)
    score, detail = _score_indicators(df_ind, configs)

    # --- 強化1: 出来高フィルター（スコアにボーナス/減衰） ---
    vf = _find_cfg(configs, "volume_filter")
    if vf is not None:
        vr = volume_ratio(df, int(vf.get("sma", 20)))
        if vr is not None:
            detail["vol_ratio"] = round(vr, 2)
            if score != 0:
                surge = float(vf.get("surge", 1.5))
                quiet = float(vf.get("quiet", 0.7))
                bonus = int(vf.get("bonus", 1))
                if vr >= surge:
                    s = 1 if score > 0 else -1
                    score += s * bonus
                    detail["volume"] = s * bonus
                elif vr < quiet:
                    score = int(score / 2)   # 0 方向へ減衰
                    detail["volume"] = "quiet"

    def _direction(sc: int) -> str:
        return "buy" if sc >= buy_threshold else "sell" if sc <= sell_threshold else "neutral"

    direction = _direction(score)

    # --- 強化2: 週足トレンド足切り（逆行する向きを block / penalty） ---
    wf = _find_cfg(configs, "weekly_trend_filter")
    if wf is not None:
        wt = weekly_trend(df, int(wf.get("sma", 13)))
        detail["weekly_trend"] = wt
        mode = wf.get("mode", "penalty")
        opposing = (direction == "buy" and wt == "down") or (direction == "sell" and wt == "up")
        if opposing:
            if mode == "block":
                detail["weekly_filter"] = "blocked"
                direction = "neutral"
            else:  # penalty: 逆方向へ 2 減点して再判定
                score += -2 if direction == "buy" else 2
                detail["weekly_filter"] = -2 if direction == "buy" else 2
                direction = _direction(score)

    # --- 地合いレジームの一次ゲート（指数版の足切り） ---
    if regime is not None:
        detail["regime"] = regime
        rf = _find_cfg(configs, "market_regime")   # _find_cfg は params dict を返す
        if rf is not None and regime == "risk_off" and direction == "buy":
            mode = rf.get("mode", "penalty")
            penalty = int(rf.get("penalty", 2))
            if mode == "block":
                detail["regime_filter"] = "blocked"
                direction = "neutral"
            else:
                score -= penalty
                detail["regime_filter"] = -penalty
                direction = _direction(score)

    return score, direction, detail


def _sma_last(series: pd.Series, length: int):
    v = series.rolling(length).mean().iloc[-1]
    return None if pd.isna(v) else float(v)


def build_plan(df: pd.DataFrame, direction: str, score: int,
               configs: list[dict[str, Any]] | None = None) -> dict:
    """作戦ボード1行分を組み立てる（強化3・4）。

    ATR から損切/利確を、サポート/MA/ATR から提案指値を算出する。
    direction が neutral の場合も close・vol/週足は埋め、指値類は None。
    戻り値: {limit_price, stop_price, target_price, atr, rationale}
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    p = _find_cfg(configs, "atr_exit") or {}
    length = int(p.get("length", 14))
    stop_mult = float(p.get("stop_mult", 1.5))
    target_mult = float(p.get("target_mult", 1.5))
    method = p.get("limit_method", "ma")
    support_n = int(p.get("support_n", 20))
    limit_ma = int(p.get("limit_ma", 5))          # 指値方式=ma で使う移動平均の期間
    entry_atr_mult = float(p.get("entry_atr_mult", 0.5))  # 指値方式=atr の押し目の深さ

    close = float(df["close"].iloc[-1])
    atr = atr_value(df, length)
    out: dict[str, Any] = {"limit_price": None, "stop_price": None,
                           "target_price": None, "atr": atr, "rationale": None}
    if atr is None:
        return out

    ma = _sma_last(df["close"], limit_ma)
    ma_val = ma if ma is not None else close

    if direction == "sell":
        out["stop_price"] = close + stop_mult * atr
        out["target_price"] = close - target_mult * atr
        resistance = float(df["high"].rolling(support_n).max().iloc[-1])
        atr_basis = close + entry_atr_mult * atr
        candidates = {"support": resistance * 0.997,
                      "ma": max(ma_val, close),     # 戻り売り: 現値より下には置かない
                      "atr": atr_basis}
        out["limit_price"] = candidates.get(method, candidates["ma"])
        out["rationale"] = (
            f"{limit_ma}日線{ma_val:.0f} / ATR戻り{atr_basis:.0f} / レジスタンス{resistance:.0f}"
            f"（方式: {method}・成行も可）")
    else:
        # buy: 新規エントリーの提案指値つき。neutral: 保有者向けの出口（利確/損切）のみ。
        out["stop_price"] = close - stop_mult * atr
        out["target_price"] = close + target_mult * atr
        if direction == "buy":
            support = float(df["low"].rolling(support_n).min().iloc[-1])
            atr_basis = close - entry_atr_mult * atr
            candidates = {"support": support * 1.003,
                          "ma": min(ma_val, close),     # 押し目買い: 現値より上には置かない
                          "atr": atr_basis}
            out["limit_price"] = candidates.get(method, candidates["ma"])
            out["rationale"] = (
                f"{limit_ma}日線{ma_val:.0f} / ATR押し目{atr_basis:.0f} / サポート{support:.0f}"
                f"（方式: {method}）")
        else:  # neutral
            out["rationale"] = f"保有者向けの出口参考（ATR{atr:.0f}）"
    return out
