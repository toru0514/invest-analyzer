"""シグナル計算コア（Phase 0）

複数のテクニカル指標・ローソク足パターンを計算し、重み付きスコアで
buy / sell / neutral を判定する。look-ahead bias を避けるため、判定には
その営業日までのデータのみを使う（pandas-ta の指標はすべて因果的＝過去と
当日のみ参照するため、全期間で一括計算したものをスライスして読んでも結果は同じ）。

Phase 1 以降（FastAPI / SQLite）でもこのモジュールをそのまま再利用する。
"""

from __future__ import annotations

import json
import math
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
    # 相対力（打ち手7）: 指数対比の N 日超過リターンを確信度に加える。enabled:0 で RS 無効。
    {"rule_type": "relative_strength",
     "params": {"period": 20, "scale": 0.10}, "weight": 1, "enabled": 1},
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

# --- 連続確信度（打ち手6）のスケール定数 ---
MACD_STRENGTH_ATR_K = 1.0      # MACDヒストを ATR 比で正規化する係数
MA_CROSS_STRENGTH_ATR_K = 2.0  # 短長MA乖離を ATR 比で正規化
OBV_STRENGTH_K = 1.0           # OBV-OBVSMA を |OBVSMA| 比で正規化
CCI_STRENGTH_SPAN = 100.0      # CCI 閾値超過分の正規化幅
DISPARITY_STRENGTH_SPAN = 7.0  # 乖離率 閾値超過分の正規化幅（%）
VOL_BOOST = 1.15       # 出来高サージ時の確信度ブースト
VOL_DISCOUNT = 0.7     # 出来高細り時（direction 生存時）の確信度ディスカウント
GATE_DISCOUNT = 0.6    # レジーム/週足ゲート penalty 時の確信度ディスカウント
RS_STRENGTH_SCALE = 0.10   # 指数対比の超過リターンを tanh で正規化する係数（20日で+10%超過→s≈0.76）
DEFAULT_RISK_PCT = 1.0   # 1トレード許容リスク（口座に対する%。1.0 = 1%）
CONF_FLOOR = 0.5         # 確信度0で許容リスクを基準の何倍まで縮めるか（自信が低い時だけ縮小）

# レジーム別グループ重み（打ち手5）。グループ純額(±GROUP_CAP)に乗じてから合算する。
# トレンド時は順張り(trend)、レンジ時は逆張り(contrarian)を主役にする。
# regime が None / 未知 → 全グループ 1（重みなし＝打ち手4の挙動）。
REGIME_GROUP_WEIGHTS: dict[str, dict[str, int]] = {
    # rs は trend 同様のモメンタム重み。risk_off だけ 1 で落ちる相場の相対力買いを抑える意図的非対称。
    "risk_on":  {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1, "rs": 2},
    "neutral":  {"trend": 1, "contrarian": 2, "volume": 1, "pattern": 1, "rs": 1},
    # risk_off も risk_on と同様に trend 主体（逆張りの「落ちるナイフ」買いの方向制御は
    # 打ち手3 のレジームゲートが別軸で担う）。risk_on と同値だが意図的な重複。
    "risk_off": {"trend": 2, "contrarian": 1, "volume": 1, "pattern": 1, "rs": 1},
}


def _group_weight(regime: str | None, group: str) -> int:
    """レジーム×グループの整数重み。未知レジーム/未知グループは 1（安全フォールバック）。"""
    return REGIME_GROUP_WEIGHTS.get(regime, {}).get(group, 1)


def _ramp_strength(value: float, low: float, high: float,
                   floor: float = 0.0, ceil: float = 100.0) -> float:
    """[floor,ceil] に収まるオシレーター（RSI/STOCH）用の符号付き強度 ∈[-1,1]。

    value<=low: 買い側 +、floor で +1（深いほど大）。value>=high: 売り側 −、ceil で −1。
    中立帯 [low,high] は 0。閾値と向きは既存 ±1 投票に整合させる。
    """
    if value <= low:
        return 0.0 if low <= floor else max(0.0, min(1.0, (low - value) / (low - floor)))
    if value >= high:
        return 0.0 if ceil <= high else -max(0.0, min(1.0, (value - high) / (ceil - high)))
    return 0.0


def _beyond_strength(value: float, low: float, high: float, span: float) -> float:
    """無界オシレーター（CCI/乖離率）用。閾値超過分を span で正規化して ∈[-1,1]。"""
    if span <= 0:
        return 0.0
    if value <= low:
        return min(1.0, (low - value) / span)
    if value >= high:
        return -min(1.0, (value - high) / span)
    return 0.0


def _tanh_strength(x: float, scale: float) -> float:
    """モメンタム系（MACD/MA乖離/OBV）用。x/scale を tanh で有界化（符号 = x の符号）。"""
    if scale <= 0:
        return 0.0
    return math.tanh(x / scale)


def relative_strength(ticker_df, index_df, n: int = 20,
                      scale: float = RS_STRENGTH_SCALE,
                      asof=None) -> float | None:
    """指数対比の相対力（超過リターン）を符号付き強度 ∈[-1,1] で返す。

    s = tanh((銘柄のn日リターン − 指数のn日リターン) / scale)。+ = 指数をアウトパフォーム（買い寄り）。
    asof（評価日 Timestamp）が与えられたら双方を asof 以前に絞ってから末尾と n 本前を取る
    （look-ahead 回避を呼び出し側でなくヘルパに閉じ込める）。データ不足/ゼロ除算は None。
    """
    def _ret(df):
        if df is None or "close" not in df:
            return None
        s = df["close"]
        if asof is not None:
            s = s[s.index <= asof]
        if len(s) < n + 1:
            return None
        base = float(s.iloc[-(n + 1)])
        if base <= 0:
            return None
        return float(s.iloc[-1]) / base - 1.0

    rt = _ret(ticker_df)
    ri = _ret(index_df)
    if rt is None or ri is None:
        return None
    return _tanh_strength(rt - ri, scale)


def _score_indicators(df: pd.DataFrame, configs: list[dict[str, Any]],
                      regime: str | None = None,
                      rs_strength: float | None = None) -> tuple[int, dict]:
    """指標列が計算済みの DataFrame の最終行をスコアリング（状態ベース・グループ化）。

    各指標は ±weight を出すが、相関グループ（順張り/逆張り/需給/パターン）ごとに合算して
    ±GROUP_CAP にクリップしてから合算する。これにより逆張り系5指標の多重カウントを止める。
    すべて当日までのデータのみ参照（look-ahead bias なし）。
    最後にグループ純額にレジーム別重み（打ち手5）を乗じて合算する：
    score = Σ weight(regime, group) × clip(group, ±GROUP_CAP)。regime=None は全重み1（＝打ち手4）。
    """
    group_raw: dict[str, int] = {}
    detail: dict = {}
    strengths: dict[str, float] = {}

    def _add(rt: str, key: str, v: int):
        detail[key] = v
        g = INDICATOR_GROUP.get(rt, rt)
        group_raw[g] = group_raw.get(g, 0) + v

    def _str(key: str, s: float):
        strengths[key] = float(s)

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
            _str("rsi", _ramp_strength(rsi, p.get("low", 30), p.get("high", 70)))
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
                    _atr = atr_value(df)
                    _str("ma_cross", _tanh_strength(cs - cl, MA_CROSS_STRENGTH_ATR_K * _atr) if _atr else 0.0)
                    if cs > cl:
                        _add(rt, "ma_cross", +w)
                    elif cs < cl:
                        _add(rt, "ma_cross", -w)

        elif rt == "macd":
            f, s, sig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
            hist = _val(df, f"MACDh_{f}_{s}_{sig}", -1)
            if hist is None:
                continue
            _atr = atr_value(df)
            _str("macd", _tanh_strength(hist, MACD_STRENGTH_ATR_K * _atr) if _atr else 0.0)
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
            _band = (cur_upper - cur_lower)
            _str("bbands", _beyond_strength(cur_close, cur_lower, cur_upper, _band / 2) if _band > 0 else 0.0)
            if cur_close <= cur_lower:
                _add(rt, "bbands", +w)
            elif cur_close >= cur_upper:
                _add(rt, "bbands", -w)

        elif rt == "stoch":
            k, d = p.get("k", 14), p.get("d", 3)
            ck = _val(df, f"STOCHk_{k}_{d}_3", -1)
            if ck is None:
                continue
            _str("stoch", _ramp_strength(ck, p.get("low", 20), p.get("high", 80)))
            if ck < p.get("low", 20):
                _add(rt, "stoch", +w)
            elif ck > p.get("high", 80):
                _add(rt, "stoch", -w)

        elif rt == "candle_pattern":
            if (_val(df, "CDL_3WHITESOLDIERS") or 0) > 0:
                _add(rt, "3whitesoldiers", +w)
                _str("3whitesoldiers", 1.0)
            if (_val(df, "CDL_3BLACKCROWS") or 0) < 0:
                _add(rt, "3blackcrows", -w)
                _str("3blackcrows", -1.0)
            eng = _val(df, "CDL_ENGULFING") or 0
            if eng > 0:
                _add(rt, "engulfing", +w)
                _str("engulfing", 1.0)
            elif eng < 0:
                _add(rt, "engulfing", -w)
                _str("engulfing", -1.0)

        elif rt == "disparity":
            ma_len = int(p.get("ma", 25))
            ma = _sma(df["close"], ma_len).iloc[-1]
            cur_close = _val(df, "close", -1)
            if pd.isna(ma) or ma == 0 or cur_close is None:
                continue
            disp = (cur_close - ma) / ma * 100
            _str("disparity", _beyond_strength(disp, p.get("low", -7), p.get("high", 7), DISPARITY_STRENGTH_SPAN))
            if disp <= p.get("low", -7):
                _add(rt, "disparity", +w)
            elif disp >= p.get("high", 7):
                _add(rt, "disparity", -w)

        elif rt == "obv":
            obv, obv_sma = obv_vs_sma(df, int(p.get("sma", 20)))
            if obv is None:
                continue
            _str("obv", _tanh_strength(obv - obv_sma, OBV_STRENGTH_K * abs(obv_sma)) if obv_sma else 0.0)
            if obv > obv_sma:
                _add(rt, "obv", +w)
            elif obv < obv_sma:
                _add(rt, "obv", -w)

        elif rt == "cci":
            cci = cci_value(df, int(p.get("length", 20)))
            if cci is None:
                continue
            _str("cci", _beyond_strength(cci, p.get("low", -100), p.get("high", 100), CCI_STRENGTH_SPAN))
            if cci <= p.get("low", -100):
                _add(rt, "cci", +w)
            elif cci >= p.get("high", 100):
                _add(rt, "cci", -w)

        elif rt == "price_target":
            continue   # スコア対象外（即通知の別経路）

    groups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in group_raw.items()}
    detail["_groups"] = groups            # クリップ後・重み適用前の純額（解釈性）
    detail["_regime"] = regime            # どのレジームで重み付けたか
    detail["_strengths"] = strengths

    # 連続強度をグループ集約 → ±GROUP_CAP クリップ → レジーム加重 → 最大重みで正規化（∈[-1,1]）
    _CANDLE_KEYS = ("3whitesoldiers", "3blackcrows", "engulfing")
    sgroup_raw: dict[str, float] = {}
    for key, s in strengths.items():
        # candle サブキーは INDICATOR_GROUP に無いので pattern へ寄せる（vote と同じグループ扱い）
        g = INDICATOR_GROUP.get(key, "pattern" if key in _CANDLE_KEYS else key)
        sgroup_raw[g] = sgroup_raw.get(g, 0.0) + s
    sgroups = {g: max(-GROUP_CAP, min(GROUP_CAP, raw)) for g, raw in sgroup_raw.items()}
    # 固定4グループ分母で正規化（欠けたグループは 0 寄与）。RS 供給時のみ5グループ目に拡張する。
    _CONF_GROUPS = ("trend", "contrarian", "volume", "pattern")
    if rs_strength is not None:
        strengths["rs"] = rs_strength
        sgroups["rs"] = max(-GROUP_CAP, min(GROUP_CAP, float(rs_strength)))
        detail["rs"] = round(float(rs_strength), 3)
        _CONF_GROUPS = _CONF_GROUPS + ("rs",)
    wmax = sum(_group_weight(regime, g) * GROUP_CAP for g in _CONF_GROUPS)
    anum = sum(_group_weight(regime, g) * sgroups.get(g, 0.0) for g in _CONF_GROUPS)
    detail["_strength_net"] = (anum / wmax) if wmax else 0.0

    score = sum(_group_weight(regime, g) * v for g, v in groups.items())
    return score, detail


def evaluate(
    df: pd.DataFrame,
    configs: list[dict[str, Any]] | None = None,
    buy_threshold: int = BUY_THRESHOLD,
    sell_threshold: int = SELL_THRESHOLD,
    regime: str | None = None,
    rs_strength: float | None = None,
):
    """df: OHLCV（小文字列・古い順）。最終行についてスコア判定する。

    buy_threshold / sell_threshold は UI から調整可能（DB 保存値を渡す）。
    戻り値: (score, direction, detail)。detail には各指標の個別寄与に加え、
    detail["_groups"]（順張り/逆張り/需給/パターンのグループ別純額・打ち手4）が入る。
    打ち手5: グループ純額はレジーム別重み（risk_on/off=trend重視、neutral=contrarian重視）で
    合算され、detail["_regime"] に適用レジームが入る（detail["_groups"] は重み適用前の純額）。
    """
    if configs is None:
        configs = DEFAULT_CONFIGS
    df_ind = add_indicators(df)
    score, detail = _score_indicators(df_ind, configs, regime, rs_strength)

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

    # --- 連続確信度（打ち手6）: direction とゲート確定後に算出（detail を後検査して二重計算回避） ---
    a = float(detail.get("_strength_net", 0.0))
    if direction == "buy":
        conf = max(0.0, a) * 100.0
    elif direction == "sell":
        conf = max(0.0, -a) * 100.0
    else:
        conf = 0.0
    vol = detail.get("volume")
    if vol == "quiet":
        conf *= VOL_DISCOUNT
    elif isinstance(vol, (int, float)) and not isinstance(vol, bool) and vol != 0:
        conf = min(100.0, conf * VOL_BOOST)
    if isinstance(detail.get("regime_filter"), int):   # penalty 発火（block は direction=neutral 済み）
        conf *= GATE_DISCOUNT
    if isinstance(detail.get("weekly_filter"), int):
        conf *= GATE_DISCOUNT
    detail["confidence"] = round(max(0.0, min(100.0, conf)), 1)

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


def position_size(entry, stop, account, risk_pct, confidence=None) -> dict:
    """リスクベースのサイジング（買い＝新規ロング用）。

    株数 = (account × eff_risk_pct%) ÷ (entry − stop)。
    confidence∈[0,100] を与えると許容リスクを CONF_FLOOR〜1.0 倍で微調整（基準は超えない）。
    引数が None・非正、または risk_per_share ≤ 0 のときは全ゼロの安全な結果を返す（例外は投げない）。
    戻り値: {shares, risk_amount, risk_per_share, position_value, effective_risk_pct}
    """
    zero = {"shares": 0.0, "risk_amount": 0.0, "risk_per_share": 0.0,
            "position_value": 0.0, "effective_risk_pct": 0.0}
    if entry is None or stop is None or account is None or risk_pct is None:
        return zero
    risk_per_share = float(entry) - float(stop)
    if risk_per_share <= 0 or account <= 0 or risk_pct <= 0:
        return zero
    eff = float(risk_pct)
    if confidence is not None:
        c = max(0.0, min(100.0, float(confidence)))
        eff = risk_pct * (CONF_FLOOR + (1.0 - CONF_FLOOR) * c / 100.0)
    risk_amount = account * eff / 100.0
    shares = risk_amount / risk_per_share
    return {"shares": shares, "risk_amount": risk_amount,
            "risk_per_share": risk_per_share, "position_value": shares * float(entry),
            "effective_risk_pct": eff}
