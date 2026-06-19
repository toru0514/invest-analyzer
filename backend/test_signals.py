"""ネットワーク不要のスモークテスト。

    backend/venv/bin/python -m pytest backend/test_signals.py
  もしくは
    backend/venv/bin/python backend/test_signals.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import signals
from backtest import INITIAL_CAPITAL, run_backtest
from market import synthetic_history
from signals import evaluate, DEFAULT_CONFIGS, market_regime, regime_series


def test_evaluate_returns_valid_direction():
    df = synthetic_history("TEST.T", n=120, seed=42)
    score, direction, detail = signals.evaluate(df)
    assert isinstance(score, int)
    assert direction in ("buy", "sell", "neutral")
    assert isinstance(detail, dict)


def test_golden_dead_cross_are_exclusive():
    df = synthetic_history("TEST.T", n=120, seed=1)
    g = signals.golden_cross(df, 5, 25)
    d = signals.dead_cross(df, 5, 25)
    assert not (g and d)


def test_backtest_reports_all_required_metrics():
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T"])}
    r = run_backtest(hist)
    for key in ("initial", "final", "pnl_amount", "pnl_pct",
                "trade_count", "win_rate", "max_drawdown_pct"):
        assert key in r
    assert r["initial"] == INITIAL_CAPITAL
    assert r["trade_count"] >= 0
    assert r["max_drawdown_pct"] >= 0


def test_trades_execute_when_thresholds_low():
    # 閾値を緩めれば（±1）売買が成立することを確認する。
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist, buy_threshold=1, sell_threshold=-1)
    assert r["trade_count"] > 0
    assert r["final"] > 0


def _base_configs():
    # 追補版の補正フィルター（出来高/週足/ATR）を除いた、状態ベースの6指標のみ。
    return [c for c in signals.DEFAULT_CONFIGS
            if c["rule_type"] not in ("volume_filter", "weekly_trend_filter", "atr_exit")]


def test_state_based_scoring_reaches_default_threshold():
    # グループ化でスコアレンジが ±4 に圧縮されたため、±1（1グループ一致）で約定が出ることを確認。
    # 実運用の閾値は /optimize(OOS) で決定する。
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist, configs=_base_configs(), buy_threshold=1, sell_threshold=-1)
    assert r["trade_count"] > 0


def test_volume_ratio_and_filter_bonus():
    import numpy as np
    import pandas as pd
    df = synthetic_history("TEST.T", n=120, seed=3)
    # 当日出来高を平均比で大きく/小さくして vol_ratio を確認
    df.loc[df.index[-1], "volume"] = df["volume"].iloc[-30:-1].mean() * 3
    vr = signals.volume_ratio(df, sma=20)
    assert vr is not None and vr > 1.5

    # score!=0 のとき surge でボーナスが付く（同方向に増える）
    cfg_no_vol = _base_configs()
    cfg_with_vol = _base_configs() + [
        {"rule_type": "volume_filter",
         "params": {"sma": 20, "surge": 1.5, "quiet": 0.7, "bonus": 1}, "weight": 1, "enabled": 1}]
    s0, _, _ = signals.evaluate(df, cfg_no_vol)
    s1, _, det = signals.evaluate(df, cfg_with_vol)
    if s0 > 0:
        assert s1 >= s0 and det.get("volume") == 1
    elif s0 < 0:
        assert s1 <= s0


def test_weekly_trend_block_suppresses_counter_trend():
    import numpy as np
    import pandas as pd
    # 明確な下降トレンドの日足を作る → 週足 down
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-15"), periods=160)
    close = np.linspace(3000, 1500, len(idx))
    df = pd.DataFrame({"open": close, "high": close + 5, "low": close - 5,
                       "close": close, "volume": 1_000_000.0}, index=idx)
    assert signals.weekly_trend(df) == "down"

    # block モードでは buy 方向（仮に出ても）が neutral に丸められる方向に働く
    cfg = _base_configs() + [
        {"rule_type": "weekly_trend_filter", "params": {"sma": 13, "mode": "block"},
         "weight": 1, "enabled": 1}]
    _, direction, det = signals.evaluate(df, cfg)
    assert direction in ("sell", "neutral")  # 下降中に buy は出さない


def test_build_plan_exits_are_ordered():
    df = synthetic_history("TEST.T", n=120, seed=7)
    close = float(df["close"].iloc[-1])
    buy = signals.build_plan(df, "buy", 3)
    assert buy["atr"] is not None
    assert buy["stop_price"] < close < buy["target_price"]
    # 既定の買い指値（5日線方式）は現値より上に置かない（押し目買い）
    assert buy["limit_price"] <= close
    sell = signals.build_plan(df, "sell", -3)
    assert sell["target_price"] < close < sell["stop_price"]
    assert sell["limit_price"] >= close   # 戻り売りは現値より下に置かない

    # 中立でも保有者向けの出口（利確/損切）は出す。提案指値は出さない。
    neutral = signals.build_plan(df, "neutral", 0)
    assert neutral["limit_price"] is None
    assert neutral["stop_price"] < close < neutral["target_price"]


def test_build_plan_limit_method_switch():
    df = synthetic_history("TEST.T", n=120, seed=7)
    common = [{"rule_type": "atr_exit", "weight": 1, "enabled": 1, "params": {
        "length": 14, "stop_mult": 1.5, "target_mult": 1.5, "support_n": 20,
        "limit_ma": 5, "entry_atr_mult": 0.5, "limit_method": "ma"}}]

    def limit(method):
        cfg = [dict(common[0], params={**common[0]["params"], "limit_method": method})]
        return signals.build_plan(df, "buy", 3, cfg)["limit_price"]

    # 方式で値が変わる。ma と atr は現値寄り、support（20日安値）は最も低い。
    ma, atr, support = limit("ma"), limit("atr"), limit("support")
    assert support <= ma and support <= atr


def test_disparity_votes_buy_when_far_below_ma():
    import numpy as np
    import pandas as pd
    # 高値圏からの急落で、終値が MA25 から大きく下に乖離する状況を作る。
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-15"), periods=60)
    close = np.concatenate([np.full(55, 1000.0), np.linspace(1000, 800, 5)])
    df = pd.DataFrame({"open": close, "high": close + 2, "low": close - 2,
                       "close": close, "volume": 1_000_000.0}, index=idx)
    cfg = [{"rule_type": "disparity", "params": {"ma": 25, "low": -7, "high": 7},
            "weight": 1, "enabled": 1}]
    df_ind = signals.add_indicators(df)
    score, detail = signals._score_indicators(df_ind, cfg)
    assert detail.get("disparity") == 1 and score == 1


def test_obv_votes_with_volume_trend():
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-15"), periods=60)
    close = np.linspace(1000, 1200, 60)          # 一貫した上昇
    df = pd.DataFrame({"open": close, "high": close + 2, "low": close - 2,
                       "close": close, "volume": 1_000_000.0}, index=idx)
    obv, obv_sma = signals.obv_vs_sma(df, 20)
    assert obv is not None and obv > obv_sma      # 上昇トレンドでは OBV が SMA を上回る
    cfg = [{"rule_type": "obv", "params": {"sma": 20}, "weight": 1, "enabled": 1}]
    score, detail = signals._score_indicators(signals.add_indicators(df), cfg)
    assert detail.get("obv") == 1 and score == 1


def test_cci_votes_buy_when_oversold():
    import numpy as np
    import pandas as pd
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-15"), periods=40)
    close = np.concatenate([np.full(35, 1000.0), np.linspace(1000, 880, 5)])
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                       "close": close, "volume": 1_000_000.0}, index=idx)
    assert signals.cci_value(df, 20) <= -100      # 急落で売られすぎ
    cfg = [{"rule_type": "cci", "params": {"length": 20, "low": -100, "high": 100},
            "weight": 1, "enabled": 1}]
    score, detail = signals._score_indicators(signals.add_indicators(df), cfg)
    assert detail.get("cci") == 1 and score == 1


def test_resolve_configs_overrides_per_ticker():
    common = [
        {"rule_type": "rsi", "params": {}, "weight": 1, "enabled": 1},
        {"rule_type": "atr_exit", "params": {"target_mult": 1.5}, "weight": 1, "enabled": 1},
    ]
    ticker = [
        {"rule_type": "atr_exit", "ticker": "8306.T", "params": {"target_mult": 1.2}, "weight": 1, "enabled": 1},
        {"rule_type": "price_target", "ticker": "8306.T", "params": {"above": 3500}, "weight": 1, "enabled": 1},
    ]
    resolved = signals.resolve_configs(common, ticker)
    atr = [c for c in resolved if c["rule_type"] == "atr_exit"]
    assert len(atr) == 1                       # 上書きで重複しない
    assert atr[0]["params"]["target_mult"] == 1.2   # 銘柄固有が優先
    assert any(c["rule_type"] == "rsi" for c in resolved)          # 上書きされない共通は残る
    assert any(c["rule_type"] == "price_target" for c in resolved)  # price_target は残る


def test_atr_exit_backtest_has_extra_metrics():
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist, exit_mode="atr")
    assert r["exit_mode"] == "plan"   # atr は plan のエイリアス（約定=提示指値）
    for key in ("take_profit_count", "stop_loss_count", "signal_exit_count",
                "avg_holding_days", "risk_reward", "equity_curve"):
        assert key in r
    # 決済回数の内訳は closed_trades と整合する
    assert (r["take_profit_count"] + r["stop_loss_count"] + r["signal_exit_count"]
            == r["closed_trades"])


def _idx(closes):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(closes))
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)


def test_market_regime_uptrend_low_dd_is_risk_on():
    assert market_regime(_idx(np.linspace(1000, 1300, 120))) == "risk_on"


def test_market_regime_downtrend_is_risk_off():
    assert market_regime(_idx(np.linspace(1300, 1000, 120))) == "risk_off"


def test_market_regime_high_drawdown_is_risk_off():
    closes = list(np.linspace(1000, 1300, 110)) + list(np.linspace(1300, 1100, 10))
    assert market_regime(_idx(closes)) == "risk_off"


def test_market_regime_short_series_is_neutral():
    assert market_regime(_idx([1000, 1010, 1005])) == "neutral"


def test_regime_series_is_causal_and_aligned():
    df = _idx(np.linspace(1000, 1300, 60))
    s = regime_series(df)
    assert len(s) == len(df)
    for i in (10, 30, 59):
        assert s.iloc[i] == market_regime(df.iloc[:i + 1])


def test_evaluate_risk_on_records_regime_no_off_gate():
    """risk_on は regime を記録するが、risk_off ゲート（打ち手3）は発火しない。

    打ち手5 で risk_on はスコア重みを変える（別テストで担保）が、方向ペナルティ（ゲート）は
    risk_off 限定のまま＝重み付けとゲートは別軸で共存する。
    """
    from signals import evaluate, DEFAULT_CONFIGS
    from market import synthetic_history
    df = synthetic_history("X.T", seed=3)
    on = evaluate(df, DEFAULT_CONFIGS, 2, -2, regime="risk_on")
    assert on[2]["regime"] == "risk_on"        # ゲート側の記録（evaluate）
    assert on[2]["_regime"] == "risk_on"        # 重み側の記録（_score_indicators）
    assert "regime_filter" not in on[2]         # risk_off ゲートは発火しない


def test_evaluate_regime_off_penalizes_a_buy_signal():
    """risk_off は買い判定にゲート減点(-2)を課す（打ち手3）。打ち手5の重み付け後に適用される。

    単一の逆張り指標(rsi)＋売られすぎの統制データで決定論化。rsi は contrarian グループで
    risk_off の重みは 1（重み付けの影響を受けない）ため、ゲート減点だけを厳密に検証できる。
    ゲートは config-gated（`evaluate` 内 `_find_cfg(configs, "market_regime")` が None だと
    発火しない）ため、cfg に market_regime ルールを必ず含める（剥がすとゲートが効かない）。
    market_regime は `_score_indicators` ではスコア対象外なので base スコアには影響しない。
    """
    cfg = [
        {"rule_type": "rsi", "params": {"length": 14, "low": 30, "high": 70},
         "weight": 1, "enabled": 1},
        {"rule_type": "market_regime",
         "params": {"mode": "penalty", "penalty": 2, "sma": 13,
                    "dd_lookback": 60, "dd_threshold": 0.10},
         "weight": 1, "enabled": 1},
    ]
    df = _declining_df()                                  # 売られすぎ → rsi が買い(+1)
    base = evaluate(df, cfg, 1, -1)                       # regime=None → score=1, buy
    assert (base[0], base[1]) == (1, "buy")
    off = evaluate(df, cfg, 1, -1, regime="risk_off")
    assert off[2]["regime_filter"] == -2                 # ゲートが発火
    assert off[0] == base[0] - 2                          # 重み付け不変(contrarian×1)＋ゲートで-2
    assert off[1] != "buy"                                # 買いが抑制される


def _declining_df(n=80):
    """単調減少（売られすぎ）で逆張り指標が一斉に買い側へ振れる合成データ。"""
    close = np.linspace(1500.0, 900.0, n)
    open_ = close + 2
    high = np.maximum(open_, close) + 3
    low = np.minimum(open_, close) - 3
    vol = np.full(n, 2_000_000.0)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_grouping_clips_high_weight_to_cap():
    cfgs = [{"rule_type": "rsi", "params": {"length": 14, "low": 30, "high": 70},
             "weight": 3, "enabled": 1}]
    score, direction, detail = evaluate(_declining_df(), cfgs, 2, -2)
    assert detail.get("rsi") == 3                  # 個別寄与は重み3のまま
    assert detail["_groups"]["contrarian"] == 1    # グループは cap=1 にクリップ
    assert score == 1


def test_grouping_caps_contrarian_multicount():
    score, direction, detail = evaluate(_declining_df(), DEFAULT_CONFIGS, 2, -2)
    fired = [k for k in ("rsi", "bbands", "stoch", "disparity", "cci") if detail.get(k, 0) > 0]
    assert len(fired) >= 2, f"複数の逆張り指標が買い側に発火する前提: {fired} / {detail}"
    assert detail["_groups"]["contrarian"] == 1


def test_score_detail_has_groups_within_cap():
    _, _, detail = evaluate(_declining_df(), DEFAULT_CONFIGS, 2, -2)
    assert "_groups" in detail and isinstance(detail["_groups"], dict)
    for g, v in detail["_groups"].items():
        assert -1 <= v <= 1


def test_score_indicators_risk_on_doubles_trend():
    """risk_on は trend グループを ×2 にする（順張り主体）。"""
    df_ind = signals.add_indicators(_idx(np.linspace(1000, 1300, 120)))  # 上昇 → trend買い
    cfg = _base_configs()
    none_score, none_detail = signals._score_indicators(df_ind, cfg)            # regime=None
    on_score, on_detail = signals._score_indicators(df_ind, cfg, "risk_on")
    assert none_detail["_groups"]["trend"] == 1                                  # 順張りが買い側
    assert on_detail["_groups"] == none_detail["_groups"]                        # 重み前グループはレジーム非依存
    assert on_score == none_score + none_detail["_groups"]["trend"]             # trend のみ ×2（他は不変）
    assert on_detail["_regime"] == "risk_on"
    assert isinstance(on_score, int)                                            # 整数重み×int → int


def test_score_indicators_neutral_doubles_contrarian():
    """neutral は contrarian グループを ×2 にする（レンジでの逆張り）。"""
    df_ind = signals.add_indicators(_declining_df())                            # 売られすぎ → 逆張り買い
    cfg = _base_configs()
    none_score, none_detail = signals._score_indicators(df_ind, cfg)
    neu_score, neu_detail = signals._score_indicators(df_ind, cfg, "neutral")
    assert none_detail["_groups"]["contrarian"] == 1
    assert neu_detail["_groups"] == none_detail["_groups"]                       # 重み前グループはレジーム非依存
    assert neu_score == none_score + none_detail["_groups"]["contrarian"]       # contrarian のみ ×2（他は不変）
    assert neu_detail["_regime"] == "neutral"


def test_score_indicators_regime_none_equals_unweighted():
    """regime=None → 全重み1 → 打ち手4と完全一致（クリップ後グループの単純合計）。"""
    df_ind = signals.add_indicators(_declining_df())
    cfg = _base_configs()
    score, detail = signals._score_indicators(df_ind, cfg)
    assert score == sum(detail["_groups"].values())
    assert detail["_regime"] is None


def test_evaluate_risk_on_amplifies_trend():
    """evaluate 経由で risk_on の trend×2 が反映される（バックテストにも自動反映される配線）。"""
    df = _idx(np.linspace(1000, 1300, 120))                     # 上昇 → trend買い
    base = evaluate(df, _base_configs(), 2, -2)                  # regime=None
    on = evaluate(df, _base_configs(), 2, -2, regime="risk_on")
    assert base[2]["_groups"]["trend"] == 1
    assert on[2]["_groups"] == base[2]["_groups"]               # 重み前グループはレジーム非依存
    assert on[0] == base[0] + 1                                  # trend×2 で +1（他は不変）
    assert on[2]["_regime"] == "risk_on"


def test_evaluate_neutral_amplifies_contrarian():
    """evaluate 経由で neutral の contrarian×2 が反映される。"""
    df = _declining_df()                                         # 売られすぎ → 逆張り買い
    base = evaluate(df, _base_configs(), 2, -2)
    neu = evaluate(df, _base_configs(), 2, -2, regime="neutral")
    assert base[2]["_groups"]["contrarian"] == 1
    assert neu[2]["_groups"] == base[2]["_groups"]             # 重み前グループはレジーム非依存
    assert neu[0] == base[0] + 1                                 # contrarian×2 で +1（他は不変）
    assert neu[2]["_regime"] == "neutral"


# --- 打ち手6: 連続強度ヘルパ ---
def test_ramp_strength_monotonic_and_bounded():
    # RSI 型（floor=0, ceil=100, 中立帯[30,70]）。低いほど買い側に強い。
    s15 = signals._ramp_strength(15, 30, 70)
    s29 = signals._ramp_strength(29, 30, 70)
    assert 0 < s29 < s15 <= 1.0            # 売られすぎが深いほど強い・単調
    assert signals._ramp_strength(50, 30, 70) == 0.0   # 中立帯は 0
    assert -1.0 <= signals._ramp_strength(85, 30, 70) < 0   # 買われすぎは負
    assert signals._ramp_strength(0, 30, 70) == 1.0    # 下限で +1
    assert signals._ramp_strength(100, 30, 70) == -1.0  # 上限で -1


def test_beyond_strength_monotonic_and_clipped():
    # CCI/乖離率 型（無界・閾値超過分を span 正規化）
    assert signals._beyond_strength(-150, -100, 100, 100) == 0.5
    assert signals._beyond_strength(-300, -100, 100, 100) == 1.0   # span 超は 1 にクリップ
    assert signals._beyond_strength(0, -100, 100, 100) == 0.0
    assert signals._beyond_strength(150, -100, 100, 100) == -0.5


def test_tanh_strength_sign_and_bounded():
    assert signals._tanh_strength(5.0, 2.0) > 0
    assert signals._tanh_strength(-5.0, 2.0) < 0
    assert abs(signals._tanh_strength(1e9, 2.0)) <= 1.0
    assert signals._tanh_strength(0.0, 2.0) == 0.0
    assert signals._tanh_strength(1.0, 0.0) == 0.0    # scale=0 はゼロ（ゼロ除算回避）


def test_score_indicators_emits_signed_strengths():
    # 売られすぎ（_declining_df）→ 逆張り指標の強度は買い側（正）。トレンドは下降（負）。
    df_ind = signals.add_indicators(_declining_df())
    score, detail = signals._score_indicators(df_ind, _base_configs())
    st = detail["_strengths"]
    assert isinstance(st, dict) and st                      # 何か発火している
    assert st.get("rsi", 0) > 0                             # 売られすぎ → rsi 買い側
    assert all(-1.0 <= v <= 1.0 for v in st.values())       # 全て有界


def test_score_indicators_strengths_are_additive_only():
    # 後方互換: _strengths を足しても従来の score / _groups は不変。
    df_ind = signals.add_indicators(_declining_df())
    score, detail = signals._score_indicators(df_ind, _base_configs())
    assert score == sum(detail["_groups"].values())        # 既存の不変条件（打ち手4）
    assert isinstance(score, int)


if __name__ == "__main__":
    test_evaluate_returns_valid_direction()
    test_golden_dead_cross_are_exclusive()
    test_backtest_reports_all_required_metrics()
    test_trades_execute_when_thresholds_low()
    test_state_based_scoring_reaches_default_threshold()
    test_volume_ratio_and_filter_bonus()
    test_weekly_trend_block_suppresses_counter_trend()
    test_build_plan_exits_are_ordered()
    test_build_plan_limit_method_switch()
    test_disparity_votes_buy_when_far_below_ma()
    test_obv_votes_with_volume_trend()
    test_cci_votes_buy_when_oversold()
    test_resolve_configs_overrides_per_ticker()
    test_atr_exit_backtest_has_extra_metrics()
    print("all smoke tests passed")
