"""ネットワーク不要のスモークテスト。

    backend/venv/bin/python -m pytest backend/test_signals.py
  もしくは
    backend/venv/bin/python backend/test_signals.py
"""

from __future__ import annotations

import signals
from backtest import INITIAL_CAPITAL, run_backtest
from market import synthetic_history


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
    # 状態ベース設計では既定閾値（±2）で売買が成立するはず（v2 の要）。
    # 出来高/週足フィルターは別途検証するため、ここでは基本6指標で評価する。
    hist = {tk: synthetic_history(tk, seed=i)
            for i, tk in enumerate(["8306.T", "7203.T", "9984.T", "6758.T"])}
    r = run_backtest(hist, configs=_base_configs())
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
    assert r["exit_mode"] == "atr"
    for key in ("take_profit_count", "stop_loss_count", "signal_exit_count",
                "avg_holding_days", "risk_reward", "equity_curve"):
        assert key in r
    # 決済回数の内訳は closed_trades と整合する
    assert (r["take_profit_count"] + r["stop_loss_count"] + r["signal_exit_count"]
            == r["closed_trades"])


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
