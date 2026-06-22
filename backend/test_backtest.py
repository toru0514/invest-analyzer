"""run_backtest の約定統一・コスト・fill_rate の単体テスト（合成データ・ネット非依存）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import run_backtest
from costs import apply_costs
from signals import DEFAULT_CONFIGS


def _trend_up_df(n=120, start=1000.0, step=5.0, seed=0):
    """緩やかな上昇トレンドの合成OHLCV（買いシグナルと押し目約定が起きやすい）。"""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(np.full(n, step) + rng.normal(0, 2, n))
    close = np.maximum(close, 50)
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 3, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def test_plan_mode_returns_cost_fillrate_and_closed_pnls():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60)
    assert r["exit_mode"] == "plan"
    assert "fill_rate" in r and (r["fill_rate"] is None or 0.0 <= r["fill_rate"] <= 1.0)
    assert "cost" in r and r["cost"]["slippage_bps"] == 10.0
    assert isinstance(r["closed_pnls"], list)


def test_atr_is_alias_of_plan():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="atr", backtest_days=60)
    assert r["exit_mode"] == "plan"   # atr は plan のエイリアス


def test_cost_reduces_pnl_vs_zero_cost():
    hist = {"X.T": _trend_up_df()}
    zero = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                        cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    costly = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                          cost={"commission_bps": 0.0, "slippage_bps": 50.0})
    # トレードが発生していれば、スリッページの大きい方が損益は小さい（同数量比較は近似）
    if zero["closed_trades"] > 0 and costly["closed_trades"] > 0:
        assert costly["pnl_pct"] <= zero["pnl_pct"]


def test_score_mode_applies_cost_and_returns_closed_pnls():
    hist = {"X.T": _trend_up_df()}
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="score", backtest_days=60)
    assert r["exit_mode"] == "score"
    assert isinstance(r["closed_pnls"], list)
    assert "cost" in r


def test_eval_start_date_restricts_trading_window():
    df = _trend_up_df()
    hist = {"X.T": df}
    split = df.index[int(len(df) * 0.7)]
    r = run_backtest(hist, configs=DEFAULT_CONFIGS, exit_mode="plan",
                     backtest_days=len(df), eval_start_date=split)
    # 約定はすべて split 以降
    for t in r["trades"]:
        assert pd.Timestamp(t["date"]) >= split.normalize()


def test_plan_entry_fills_at_build_plan_limit(monkeypatch):
    """plan モードのエントリーは build_plan の limit_price で約定する（旧 close-0.5*ATR ではない）。

    evaluate と build_plan を固定し、確実に買いシグナル＋到達可能な提示指値を出して
    「約定価格＝build_plan の limit_price＋スリッページ」を決定論的に検証（設計§5.2 検証=提示）。
    """
    import backtest as bt_mod

    df = _trend_up_df(n=80)
    cost = {"commission_bps": 0.0, "slippage_bps": 10.0}
    proposed: set[float] = set()

    def fake_plan(window, direction, score, configs=None):
        # 当日終値より十分高い指値＝翌日ほぼ確実に約定。旧ロジック close-0.5*ATR とは明確に別値。
        limit = float(window["close"].iloc[-1]) * 1.5
        proposed.add(round(apply_costs(limit, "buy", cost), 6))
        return {"limit_price": limit, "stop_price": limit * 0.5,
                "target_price": limit * 3.0, "atr": 10.0, "rationale": "x"}

    monkeypatch.setattr(bt_mod, "evaluate", lambda *a, **k: (3, "buy", {}))
    monkeypatch.setattr(bt_mod, "build_plan", fake_plan)

    r = bt_mod.run_backtest({"X.T": df}, configs=DEFAULT_CONFIGS, exit_mode="plan",
                            backtest_days=40, cost=cost)
    buys = [t for t in r["trades"] if t["action"] == "buy"]
    assert buys, "買い約定が発生していること"
    # 約定価格は build_plan の提示指値＋スリッページ（旧 close-0.5*ATR なら別値になる）
    for t in buys:
        assert round(t["price"], 6) in proposed


def test_regime_at_is_asof():
    from backtest import _regime_at
    s = pd.Series({pd.Timestamp("2026-01-05"): "risk_on",
                   pd.Timestamp("2026-01-10"): "risk_off"})
    assert _regime_at(s, pd.Timestamp("2026-01-07")) == "risk_on"   # 直近以前
    assert _regime_at(s, pd.Timestamp("2026-01-10")) == "risk_off"
    assert _regime_at(s, pd.Timestamp("2026-01-01")) is None        # 系列開始前 → None
    assert _regime_at(None, pd.Timestamp("2026-01-07")) is None


def test_run_backtest_passes_asof_regime_to_evaluate(monkeypatch):
    """run_backtest が各意思決定日のレジーム（asof）を evaluate に届けていることを検証（配線）。"""
    import backtest as bt_mod
    from signals import regime_series
    stock = _trend_up_df(n=120, seed=5)
    idx_close = np.linspace(1300, 1000, 120)            # 同期間の下降指数 → ほぼ risk_off
    index_df = pd.DataFrame({"open": idx_close, "high": idx_close, "low": idx_close,
                             "close": idx_close, "volume": np.full(120, 1e6)},
                            index=stock.index)
    rs = regime_series(index_df)

    seen = []
    real = bt_mod.evaluate

    def spy(df, configs, bth, sth, regime=None, rs_strength=None):
        seen.append(regime)
        return real(df, configs, bth, sth, regime=regime, rs_strength=rs_strength)

    monkeypatch.setattr(bt_mod, "evaluate", spy)
    bt_mod.run_backtest({"X.T": stock}, configs=DEFAULT_CONFIGS, exit_mode="plan",
                        backtest_days=60, regime_series=rs)
    # レジームが evaluate に届き、下降指数なので risk_off が含まれる（asof・causal）
    assert any(r == "risk_off" for r in seen)
    assert all(r in ("risk_on", "neutral", "risk_off") for r in seen if r is not None)


def test_run_backtest_rs_none_is_backward_compatible():
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    base = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2)
    # index_history/rs_params 未指定 = 従来結果（実戻り値キーで固定）
    explicit = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2,
                                     index_history=None, rs_params=None)
    assert base["pnl_pct"] == explicit["pnl_pct"]
    assert base["trade_count"] == explicit["trade_count"]


def test_run_backtest_rs_supplied_runs_and_keeps_trades():
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    idx = synthetic_history("IDX.T", n=120, seed=99)
    base = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2)
    rs = backtest.run_backtest(hist, buy_threshold=2, sell_threshold=-2,
                               index_history=idx, rs_params={"period": 20, "scale": 0.10})
    # RS は score/direction を動かさない（score モード）→ 売買・PnL は不変
    assert rs["trade_count"] == base["trade_count"]
    assert rs["pnl_pct"] == base["pnl_pct"]


def test_run_backtest_plan_rs_structure_invariant():
    """RS 供給で build_plan の指値は不変＝約定の件数と約定価格は不変。
    ただし confidence（RS 依存）がサイジングに入るため pnl/株数は変わってよい（打ち手8）。"""
    from market import synthetic_history
    import backtest
    hist = {f"P{i}.T": synthetic_history(f"P{i}.T", n=120, seed=i) for i in range(2)}
    idx = synthetic_history("IDX.T", n=120, seed=77)
    base = backtest.run_backtest(hist, configs=None, exit_mode="plan", backtest_days=40,
                                 buy_threshold=2, sell_threshold=-2, initial_capital=5_000_000)
    rs = backtest.run_backtest(hist, configs=None, exit_mode="plan", backtest_days=40,
                               buy_threshold=2, sell_threshold=-2, initial_capital=5_000_000,
                               index_history=idx, rs_params={"period": 20, "scale": 0.10})
    assert rs["closed_trades"] == base["closed_trades"]
    # 約定価格列（買い/売りの fill）は RS 非依存（選定 seed では RS が direction を反転させない前提）
    base_prices = [round(t["price"], 6) for t in base["trades"]]
    rs_prices = [round(t["price"], 6) for t in rs["trades"]]
    assert base_prices == rs_prices


def test_run_backtest_plan_risk_sizes_by_stop_width():
    """大資本で risk_pct が大きいほど建玉（総買い株数）が増える＝リスクサイジングが効く。"""
    import numpy as np, pandas as pd
    import backtest
    # 決定論的な上昇トレンド（buy を確実に出す。high/low にスプレッドを付与し ATR を確保）
    closes = np.linspace(1000.0, 1400.0, 160)
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(closes))
    up = pd.DataFrame({"open": closes, "high": closes + 10, "low": closes - 10,
                       "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)
    hist = {"UP.T": up}
    kw = dict(configs=None, exit_mode="plan", backtest_days=120, buy_threshold=1,
              sell_threshold=-1, initial_capital=50_000_000)   # キャップに張り付かない大資本
    small = backtest.run_backtest(hist, risk_pct=0.2, **kw)
    large = backtest.run_backtest(hist, risk_pct=2.0, **kw)
    buys_small = sum(t["shares"] for t in small["trades"] if t["action"] == "buy")
    buys_large = sum(t["shares"] for t in large["trades"] if t["action"] == "buy")
    assert buys_small > 0 and buys_large > 0, "buy が一度も約定していない＝テストが空虚"
    assert buys_large > buys_small        # リスク許容が大きいほど株数が多い


def test_run_backtest_plan_caps_at_bucket_cash():
    """小資本では desired がバケットを超え、全力買い（投資額 ≤ バケット現金）に縮退する。"""
    from market import synthetic_history
    import backtest
    hist = {"A.T": synthetic_history("A.T", n=120, seed=1),
            "B.T": synthetic_history("B.T", n=120, seed=2)}
    r = backtest.run_backtest(hist, configs=None, exit_mode="plan", backtest_days=40,
                              buy_threshold=2, sell_threshold=-2, initial_capital=3000, risk_pct=1.0)
    bucket = 3000 / 2
    for t in r["trades"]:
        if t["action"] == "buy":
            assert t["shares"] * t["price"] <= bucket + 1e-6   # バケット現金を超えない


def test_plan_exit_params_default_off_is_unchanged():
    """trail/time 既定OFFは現挙動を完全再現（約定価格・closed_pnls・既存countが不変）。"""
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    kw = dict(configs=None, exit_mode="plan", backtest_days=40, buy_threshold=2,
              sell_threshold=-2, initial_capital=5_000_000)
    base = backtest.run_backtest(hist, **kw)
    explicit = backtest.run_backtest(hist, trail_atr_mult=0.0, max_hold_days=0, **kw)
    assert base["closed_pnls"] == explicit["closed_pnls"]
    assert [round(t["price"], 6) for t in base["trades"]] == \
           [round(t["price"], 6) for t in explicit["trades"]]
    assert base["take_profit_count"] == explicit["take_profit_count"]
    assert base["stop_loss_count"] == explicit["stop_loss_count"]
    assert explicit["trail_exit_count"] == 0 and explicit["time_exit_count"] == 0


def test_plan_trailing_exit_locks_profit(monkeypatch):
    """上昇後の押し目でトレーリングstopが利益を確保して決済（理由 trail・固定targetは無効）。"""
    import backtest as bt_mod
    import numpy as np, pandas as pd
    closes = [100, 100, 105, 130, 150, 170, 190, 160]
    highs  = [100, 102, 108, 132, 152, 172, 192, 175]
    lows   = [100,  98, 104, 128, 148, 168, 188, 160]   # low[2]=104 で limit 約定
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(closes))
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)

    calls = {"n": 0}
    def fake_eval(window, configs, bth, sth, regime=None, rs_strength=None):
        calls["n"] += 1
        return (3, "buy", {"confidence": None}) if calls["n"] == 1 else (0, "neutral", {})
    def fake_plan(window, direction, score, configs=None):
        return {"limit_price": 104.0, "stop_price": 80.0, "target_price": 999.0,
                "atr": 10.0, "rationale": "x"}
    monkeypatch.setattr(bt_mod, "evaluate", fake_eval)
    monkeypatch.setattr(bt_mod, "build_plan", fake_plan)

    kw = dict(configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=len(closes),
              warmup_days=1, initial_capital=1_000_000,
              cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    r = bt_mod.run_backtest({"X.T": df}, trail_atr_mult=3.0, **kw)
    assert r["trail_exit_count"] >= 1          # トレーリングで決済した
    assert r["take_profit_count"] == 0         # 固定 target は無効化される
    assert any(p > 0 for p in r["closed_pnls"])  # 利益を確保した決済がある

    calls["n"] = 0                              # fake_eval カウンタを戻して固定モードで再実行
    fixed = bt_mod.run_backtest({"X.T": df}, trail_atr_mult=0.0, **kw)
    assert fixed["trail_exit_count"] == 0      # 固定モードに trail 決済は無い


def test_plan_trailing_stop_is_lookahead_safe(monkeypatch):
    """当日高値は当日のトレーリングstopに効かない（high_water は前バーまで）。
    i=3 の急騰高値で当日決済されず、i=5 で前バーまでの high_water 基準に決済される。"""
    import backtest as bt_mod
    import numpy as np, pandas as pd
    closes = [100, 100, 105, 175, 175, 170, 165]
    highs  = [100, 102, 108, 200, 180, 175, 170]   # i=3 で 200 に急騰
    lows   = [100,  98, 104, 170, 171, 169, 160]   # low[2]=104 約定
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(closes))
    df = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                       "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)

    calls = {"n": 0}
    def fake_eval(window, configs, bth, sth, regime=None, rs_strength=None):
        calls["n"] += 1
        return (3, "buy", {"confidence": None}) if calls["n"] == 1 else (0, "neutral", {})
    def fake_plan(window, direction, score, configs=None):
        return {"limit_price": 104.0, "stop_price": 80.0, "target_price": 999.0,
                "atr": 10.0, "rationale": "x"}
    monkeypatch.setattr(bt_mod, "evaluate", fake_eval)
    monkeypatch.setattr(bt_mod, "build_plan", fake_plan)

    r = bt_mod.run_backtest({"X.T": df}, configs=DEFAULT_CONFIGS, exit_mode="plan",
                            backtest_days=len(closes), warmup_days=1, trail_atr_mult=3.0,
                            initial_capital=1_000_000,
                            cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    assert r["trail_exit_count"] == 1
    # 当日高値で決済していれば days=1（i=3）。前バーまでの high_water なら days=3（i=5）。
    assert r["avg_holding_days"] == 3.0
    sells = [t for t in r["trades"] if t["action"] == "sell"]
    assert len(sells) == 1 and round(sells[0]["price"], 6) == 170.0


def test_plan_time_exit_closes_stale_position(monkeypatch):
    """stop/targetに当たらない横ばい建玉を max_hold_days で終値決済（理由 time）。"""
    import backtest as bt_mod
    import numpy as np, pandas as pd
    flat = [100, 100, 100, 100, 100, 100, 100, 100]
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(flat))
    df = pd.DataFrame({"open": flat, "high": [c + 1 for c in flat], "low": [c - 1 for c in flat],
                       "close": flat, "volume": np.full(len(flat), 1e6)}, index=idx)
    calls = {"n": 0}
    def fake_eval(window, configs, bth, sth, regime=None, rs_strength=None):
        calls["n"] += 1
        return (3, "buy", {"confidence": None}) if calls["n"] == 1 else (0, "neutral", {})
    def fake_plan(window, direction, score, configs=None):
        return {"limit_price": 100.0, "stop_price": 50.0, "target_price": 999.0,
                "atr": 10.0, "rationale": "x"}
    monkeypatch.setattr(bt_mod, "evaluate", fake_eval)
    monkeypatch.setattr(bt_mod, "build_plan", fake_plan)

    r = bt_mod.run_backtest({"X.T": df}, configs=DEFAULT_CONFIGS, exit_mode="plan",
                            backtest_days=len(flat), warmup_days=1, max_hold_days=3,
                            initial_capital=1_000_000,
                            cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    assert r["time_exit_count"] >= 1
    assert r["avg_holding_days"] == 3.0    # 保有上限ちょうどで決済（i-entry_i==3）


def _earnings_df():
    """i=2 で約定し、i=5 が決算翌日の窓バー（寄り70で stop95 を窓抜け）になる統制OHLC。"""
    import numpy as np, pandas as pd
    opens  = [100, 100, 104, 110, 110,  70,  72,  72]
    closes = [100, 100, 104, 110, 110,  72,  72,  72]
    highs  = [100, 102, 106, 112, 112,  75,  74,  74]
    lows   = [100,  99, 104, 106, 106,  65,  70,  70]
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(closes))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                         "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)


def _earnings_eval_plan(monkeypatch):
    """最初だけ buy・以降 neutral／固定プラン（limit104・stop95・target999）。

    戻り値 (bt_mod, calls)。同じ monkeypatch で run_backtest を複数回呼ぶときは、
    呼び出し間で calls["n"] = 0 にリセットして「最初だけ buy」を再現する
    （既存 test_plan_trailing_exit_locks_profit と同型）。
    """
    import backtest as bt_mod
    calls = {"n": 0}
    def fake_eval(window, configs, bth, sth, regime=None, rs_strength=None):
        calls["n"] += 1
        return (3, "buy", {"confidence": None}) if calls["n"] == 1 else (0, "neutral", {})
    def fake_plan(window, direction, score, configs=None):
        return {"limit_price": 104.0, "stop_price": 95.0, "target_price": 999.0,
                "atr": 10.0, "rationale": "x"}
    monkeypatch.setattr(bt_mod, "evaluate", fake_eval)
    monkeypatch.setattr(bt_mod, "build_plan", fake_plan)
    return bt_mod, calls


def test_plan_earnings_default_off_is_unchanged():
    """earnings_map=None（既定）は現挙動を完全再現（約定価格・closed_pnls・新count=0）。"""
    from market import synthetic_history
    import backtest
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=120, seed=i) for i in range(3)}
    kw = dict(configs=None, exit_mode="plan", backtest_days=40, buy_threshold=2,
              sell_threshold=-2, initial_capital=5_000_000)
    base = backtest.run_backtest(hist, **kw)
    explicit = backtest.run_backtest(hist, earnings_map=None, earnings_exit_days=0, **kw)
    assert base["closed_pnls"] == explicit["closed_pnls"]
    assert [round(t["price"], 6) for t in base["trades"]] == \
           [round(t["price"], 6) for t in explicit["trades"]]
    assert explicit["gap_exit_count"] == 0 and explicit["earnings_exit_count"] == 0


def test_plan_earnings_gap_fills_at_open(monkeypatch):
    """earnings_aware（earnings_map 供給・exit_days=0）の持ち越しは、決算翌日の窓を寄りfillで再現する。"""
    bt_mod, calls = _earnings_eval_plan(monkeypatch)
    df = _earnings_df()
    E = df.index[4]   # searchsorted(side=right) → 窓バー i=5
    kw = dict(configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=len(df),
              warmup_days=1, initial_capital=1_000_000,
              cost={"commission_bps": 0.0, "slippage_bps": 0.0})

    on = bt_mod.run_backtest({"X.T": df}, earnings_map={"X.T": [E]}, earnings_exit_days=0, **kw)
    sells_on = [t for t in on["trades"] if t["action"] == "sell"]
    assert on["gap_exit_count"] == 1
    assert len(sells_on) == 1 and round(sells_on[0]["price"], 6) == 70.0   # 寄り70（stop95 ではない）

    # 比較: earnings OFF だと同じ i=5 で stop ぴったり95 約定＝持ち越しコストを過小評価。
    # 同じ monkeypatch を再利用するので「最初だけ buy」を再現するためカウンタをリセットする。
    calls["n"] = 0
    off = bt_mod.run_backtest({"X.T": df}, earnings_map=None, earnings_exit_days=0, **kw)
    sells_off = [t for t in off["trades"] if t["action"] == "sell"]
    assert off["gap_exit_count"] == 0
    assert len(sells_off) == 1 and round(sells_off[0]["price"], 6) == 95.0
    assert on["closed_pnls"][0] < off["closed_pnls"][0]   # 窓fillの方が損失が大きい（誠実）
