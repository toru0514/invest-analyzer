# トレーリングストップ＋時間切れ手仕舞い 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** plan-mode バックテストにトレーリングストップと時間切れ手仕舞いを出口の選択肢として追加し、`/backtest` で比較できるようにする（加法的・後方互換・既定OFFで現挙動再現）。

**Architecture:** 純関数 `signals.trailing_stop` でラチェット計算を切り出し、`_run_backtest_plan` の保有中決済ブロックで「トレーリング/固定stop・target/時間切れ」を優先順に判定。`trail_atr_mult`/`max_hold_days` を `run_backtest`→`evaluate_holdout`→`/backtest`・`/optimize` に貫通（既定 0=OFF）。フロントは純関数 `buildBacktestBody` を介して任意入力2つを渡す。

**Tech Stack:** Python（FastAPI / pandas / numpy / pytest）、TypeScript（Next.js / vitest）。

**Spec:** `docs/superpowers/specs/2026-06-22-trailing-time-exit-design.md`

**テストコマンド:**
- backend 全体: `backend/venv/bin/python -m pytest backend/ -q`（現状122件・全緑を維持）
- backend 個別: `backend/venv/bin/python -m pytest backend/<file>::<test> -q`
- frontend 全体: `npm --prefix frontend test`（現状16件）

**重要な前提（メモリ [[signal-tests-prefer-deterministic-ohlc]]）:** シグナル/バックテストのテストは `synthetic_history(seed=...)` の seed 走査でなく、統制 OHLC＋`monkeypatch` で決定論化する。plan-mode の決済検証は `monkeypatch.setattr(bt_mod, "evaluate"/"build_plan", ...)`（既存 `test_plan_entry_fills_at_build_plan_limit` 様式）で entry/plan を固定する。

---

## Task 1: 純関数 `signals.trailing_stop`

**Files:**
- Modify: `backend/signals.py`（`position_size` の直後・行706付近に追加）
- Test: `backend/test_signals.py`（`position_size` テスト群の後ろ・行636付近に追加）

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_signals.py` の末尾付近（position_size テストの後）に追加:

```python
def test_trailing_stop_long_ratchets_up_only():
    from signals import trailing_stop
    # 高値追随で下限を上げる（mult=3, atr=20 → 距離60）
    assert trailing_stop(900.0, 1000.0, 20.0, 3.0) == 940.0    # max(900, 1000-60)
    assert trailing_stop(900.0, 950.0, 20.0, 3.0) == 900.0     # max(900, 890)=900（下げない）
    assert trailing_stop(900.0, 1100.0, 20.0, 3.0) == 1040.0   # さらに高値更新で上昇


def test_trailing_stop_short_ratchets_down_only():
    from signals import trailing_stop
    assert trailing_stop(1100.0, 1000.0, 20.0, 3.0, "sell") == 1060.0   # min(1100, 1060)
    assert trailing_stop(1100.0, 1080.0, 20.0, 3.0, "sell") == 1100.0   # min(1100, 1140)=1100


def test_trailing_stop_disabled_returns_initial():
    from signals import trailing_stop
    assert trailing_stop(900.0, 1000.0, 20.0, 0.0) == 900.0    # mult 0 → OFF
    assert trailing_stop(900.0, 1000.0, 0.0, 3.0) == 900.0     # atr 0 → OFF
    assert trailing_stop(900.0, None, 20.0, 3.0) == 900.0      # extreme None → OFF
    assert trailing_stop(900.0, 1000.0, None, 3.0) == 900.0    # atr None → OFF


def test_trailing_stop_robust_strings_and_nan():
    from signals import trailing_stop
    nan = float("nan")
    assert trailing_stop("900.0", "1000.0", "20.0", "3.0") == 940.0   # 数値文字列でも計算
    assert trailing_stop(900.0, nan, 20.0, 3.0) == 900.0              # extreme nan → OFF
    assert trailing_stop(900.0, 1000.0, nan, 3.0) == 900.0            # atr nan → OFF
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_trailing_stop_long_ratchets_up_only -q`
Expected: FAIL（`ImportError: cannot import name 'trailing_stop'`）

- [ ] **Step 3: 最小実装**

`backend/signals.py` の `position_size`（行678-706）の直後に追加:

```python
def trailing_stop(initial_stop, extreme, atr, mult, direction="buy") -> float:
    """トレーリングストップ価格を返す純関数。

    ロング(direction="buy"): max(initial_stop, extreme − mult·atr)（下限を上にのみ引き上げ）。
    ショート(direction="sell"): min(initial_stop, extreme + mult·atr)（上限を下にのみ引き下げ）。
    extreme は建玉開始以降の最有利値（ロング=最高値・ショート=最安値）。

    mult≤0 / atr≤0 / extreme が nan / 各引数 None・非数 のときは initial_stop をそのまま返す
    （トレーリング無効・例外は投げない契約。position_size と同じ堅牢方針）。
    方向対応は build_plan(buy/sell) との対称性と将来のショート側バックテスト用。現 plan-mode
    バックテストはロング経路のみ使用する（ショート経路は単体テストで固定）。
    """
    if initial_stop is None:
        return None
    try:
        s0 = float(initial_stop)
    except (TypeError, ValueError):
        return initial_stop
    try:
        ext, a, m = float(extreme), float(atr), float(mult)
    except (TypeError, ValueError):
        return s0
    # atr/mult 非正・extreme nan を弾く（nan 比較は False なので not(...)/ext==ext で拾う）
    if not (a > 0 and m > 0 and ext == ext):
        return s0
    if direction == "sell":
        return min(s0, ext + m * a)
    return max(s0, ext - m * a)
```

- [ ] **Step 4: テスト緑を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k trailing_stop`
Expected: 4 passed

- [ ] **Step 5: commit**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: トレーリングストップ純関数 signals.trailing_stop を追加（打ち手9）"
```

---

## Task 2: `_run_backtest_plan` のトレーリング出口＋配線（既定OFFで後方互換）

**Files:**
- Modify: `backend/backtest.py`
  - import 行13-14（`trailing_stop` 追加）
  - `run_backtest` シグネチャ行39-45・plan ディスパッチ行54-59
  - `_run_backtest_plan` シグネチャ行150-154・ループ変数行172-175・約定ブロック行206・保有中決済ブロック行215-227・signal 決済リセット行244・pending 生成行250-252・戻り値行291-294
- Test: `backend/test_backtest.py`（末尾に追加）

このタスクで「トレーリング＋既定OFF回帰＋look-ahead」を入れる。時間切れは Task 3。

- [ ] **Step 1: 回帰テスト（既定OFF=現挙動）を書く**

`backend/test_backtest.py` 末尾に追加:

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py::test_plan_exit_params_default_off_is_unchanged -q`
Expected: FAIL（`TypeError: run_backtest() got an unexpected keyword argument 'trail_atr_mult'`）

- [ ] **Step 3: トレーリングのテストを書く**

```python
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
```

- [ ] **Step 4: look-ahead 安全性のテストを書く**

```python
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
```

- [ ] **Step 5: 失敗を確認（トレーリング/look-ahead）**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q -k "trailing or lookahead or default_off"`
Expected: FAIL（trail_exit_count キー無し・TypeError）

- [ ] **Step 6: 実装する**

(a) `backend/backtest.py` import（行13-14）に `trailing_stop` を追加:

```python
from signals import (BUY_THRESHOLD, DEFAULT_CONFIGS, DEFAULT_RISK_PCT, SELL_THRESHOLD,
                     build_plan, evaluate, position_size, trailing_stop)
```

(b) `run_backtest` シグネチャ（行39-45）末尾に2引数追加:

```python
def run_backtest(
    histories, configs=None, initial_capital=INITIAL_CAPITAL,
    backtest_days=BACKTEST_DAYS, warmup_days=WARMUP_DAYS,
    buy_threshold=BUY_THRESHOLD, sell_threshold=SELL_THRESHOLD,
    exit_mode="score", cost=None, eval_start_date=None, regime_series=None,
    index_history=None, rs_params=None, risk_pct=DEFAULT_RISK_PCT,
    trail_atr_mult=0.0, max_hold_days=0,
) -> dict:
```

(c) plan ディスパッチ（行54-59）に貫通:

```python
    if exit_mode in ("plan", "atr"):
        return _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                                  warmup_days, buy_threshold, sell_threshold, cost,
                                  eval_start_date, regime_series,
                                  index_history=index_history, rs_params=rs_params,
                                  risk_pct=risk_pct,
                                  trail_atr_mult=trail_atr_mult, max_hold_days=max_hold_days)
```

(d) `_run_backtest_plan` シグネチャ（行150-154）に2引数追加:

```python
def _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                       warmup_days, buy_threshold, sell_threshold, cost,
                       eval_start_date, regime_series=None,
                       index_history=None, rs_params=None,
                       risk_pct=DEFAULT_RISK_PCT,
                       trail_atr_mult=0.0, max_hold_days=0) -> dict:
```

(e) ループ変数（行172-175）に `entry_atr`・`high_water` を追加:

```python
        shares = 0.0
        entry_price = stop = target = None
        entry_i = None
        entry_atr = None
        high_water = None
        pending = None   # {"limit","stop","target","expires","confidence","atr"}
```

(f) 約定確定（行206 の代入）に `entry_atr`・`high_water` を追加:

```python
                    entry_price, stop, target, entry_i = fill, pending["stop"], pending["target"], i
                    entry_atr = pending.get("atr")
                    high_water = high
```

(g) 保有中決済ブロック（行215-227）を丸ごと差し替え:

```python
            # 2) 保有中（エントリー当日を除く）：トレーリング/損切/利確
            if shares > 0 and entry_i is not None and i > entry_i:
                trailing = trail_atr_mult > 0 and entry_atr is not None
                # トレーリングstopは high_water（前バーまで）で算出。当日高値は当日stopに効かせない。
                cur_stop = trailing_stop(stop, high_water, entry_atr, trail_atr_mult, "buy") \
                    if trailing else stop
                if low <= cur_stop:
                    exit_raw, reason = cur_stop, ("trail" if trailing else "stop")
                elif not trailing and high >= target:   # 固定targetはトレーリングOFF時のみ
                    exit_raw, reason = target, "target"
                else:
                    exit_raw, reason = None, None
                # （Task 3 でここに time-exit を追加）
                if exit_raw is not None:
                    fill = apply_costs(exit_raw, "sell", cost)
                    proceeds = shares * fill
                    proceeds -= commission_cost(proceeds, cost)
                    closed.append({"pnl": proceeds - entry_price * shares,
                                   "reason": reason, "days": i - entry_i})
                    cash += proceeds
                    trades.append({"date": d, "ticker": ticker, "action": "sell",
                                   "price": fill, "shares": shares})
                    shares = 0.0; entry_price = stop = target = None
                    entry_i = None; entry_atr = None; high_water = None
                elif trailing:
                    # 未決済なら当日高値で high_water 更新（cur_stop 算出の後＝look-ahead回避）
                    high_water = max(high_water, high)
```

(h) signal 決済のリセット（行244）にも `entry_atr`・`high_water` を追加:

```python
                    shares = 0.0; entry_price = stop = target = None
                    entry_i = None; entry_atr = None; high_water = None
```

(i) pending 生成（行250-252）に `"atr"` を追加:

```python
                        pending = {"limit": plan["limit_price"], "stop": plan["stop_price"],
                                   "target": plan["target_price"], "expires": i + entry_expiry_days,
                                   "confidence": detail.get("confidence"), "atr": plan["atr"]}
```

(j) 戻り値（行291-293 の count 群）に2つ追加:

```python
        "take_profit_count": sum(1 for c in closed if c["reason"] == "target"),
        "stop_loss_count": sum(1 for c in closed if c["reason"] == "stop"),
        "signal_exit_count": sum(1 for c in closed if c["reason"] == "signal"),
        "trail_exit_count": sum(1 for c in closed if c["reason"] == "trail"),
        "time_exit_count": sum(1 for c in closed if c["reason"] == "time"),
```

- [ ] **Step 7: 追加テスト緑を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q -k "trailing or lookahead or default_off"`
Expected: 3 passed

- [ ] **Step 8: backend 全体が緑（回帰なし）を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 既存122 + 新規（Task1の4 + Task2の3）= 129 passed

- [ ] **Step 9: commit**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: plan-mode バックテストにトレーリングストップ出口を追加（既定OFF・打ち手9）"
```

---

## Task 3: `_run_backtest_plan` の時間切れ手仕舞い

**Files:**
- Modify: `backend/backtest.py`（保有中決済ブロックの「（Task 3 でここに time-exit を追加）」コメント箇所）
- Test: `backend/test_backtest.py`（末尾に追加）

- [ ] **Step 1: 時間切れのテストを書く**

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py::test_plan_time_exit_closes_stale_position -q`
Expected: FAIL（`time_exit_count` は 0・`avg_holding_days` が 3.0 でない＝決済されず保有継続）

- [ ] **Step 3: 実装する**

`backend/backtest.py` 保有中決済ブロックの「（Task 3 でここに time-exit を追加）」コメント行を次に差し替え（日中の価格トリガ判定の直後・`if exit_raw is not None:` の直前）:

```python
                # 3) 時間切れ（終値）: 日中の価格トリガが無く保有が上限到達
                if exit_raw is None and max_hold_days > 0 and (i - entry_i) >= max_hold_days:
                    exit_raw, reason = close, "time"
```

- [ ] **Step 4: テスト緑を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py::test_plan_time_exit_closes_stale_position -q`
Expected: PASS

- [ ] **Step 5: backend 全体が緑を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 130 passed

- [ ] **Step 6: commit**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: plan-mode バックテストに時間切れ手仕舞いを追加（既定OFF・打ち手9）"
```

---

## Task 4: `evaluation.evaluate_holdout` への passthrough

**Files:**
- Modify: `backend/evaluation.py`（`evaluate_holdout` シグネチャ行80-82・`_bt` 行100-106）
- Test: `backend/test_evaluation.py`（末尾に追加）

注: `benchmark` 内の `run_backtest`（行60）は score モードのため貫通しない（spec §7）。

- [ ] **Step 1: スモークテストを書く（`test_evaluate_holdout_accepts_risk_pct` に倣う）**

`backend/test_evaluation.py` 末尾に追加:

```python
def test_evaluate_holdout_accepts_exit_params():
    """evaluate_holdout が trail/time を受領して完走する（挙動差は backtest 層で担保）。"""
    from market import synthetic_history
    from evaluation import evaluate_holdout
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=160, seed=i) for i in range(2)}
    res = evaluate_holdout(hist, None, trail_atr_mult=3.0, max_hold_days=10)
    assert "out_of_sample" in res and "chosen_params" in res
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py::test_evaluate_holdout_accepts_exit_params -q`
Expected: FAIL（`TypeError: evaluate_holdout() got an unexpected keyword argument 'trail_atr_mult'`）

- [ ] **Step 3: 実装する**

(a) シグネチャ（行80-82）末尾に2引数追加:

```python
def evaluate_holdout(histories, configs, *, split_ratio=0.7, grid=None, cost=None,
                     initial_capital=3000.0, warmup_days=35, regime_series=None,
                     index_history=None, rs_params=None, risk_pct=None,
                     trail_atr_mult=0.0, max_hold_days=0) -> dict:
```

(b) `_bt` クロージャ（行100-106）の `run_backtest` 呼び出しに貫通:

```python
    def _bt(hist, th, cfgs, eval_start=None):
        return run_backtest(hist, configs=cfgs, initial_capital=initial_capital,
                            backtest_days=big, warmup_days=warmup_days,
                            buy_threshold=th, sell_threshold=-th, exit_mode="plan",
                            cost=cost, eval_start_date=eval_start, regime_series=regime_series,
                            index_history=index_history, rs_params=rs_params,
                            risk_pct=risk_pct,
                            trail_atr_mult=trail_atr_mult, max_hold_days=max_hold_days)
```

- [ ] **Step 4: テスト緑を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -q`
Expected: 全て PASS（新規1件含む）

- [ ] **Step 5: commit**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: evaluate_holdout に trail/time 出口パラメータを貫通（既定OFF・打ち手9）"
```

---

## Task 5: API 配線（`/backtest`・`/optimize`）

**Files:**
- Modify: `backend/main.py`（`BacktestIn` 行109-116・`/backtest` ハンドラ行548-551・`OptimizeIn` 行568-573・`/optimize` ハンドラ行592-594）
- Test: `backend/test_api.py`（末尾に追加）

注: `risk_pct` は永続設定（`_risk_pct()`）由来だが、trail/time は**ペイロード項目**（spec §8）。`Field` は未 import のためハンドラ内クランプで `ge=0` 相当を担保する。

- [ ] **Step 1: API テストを書く**

`backend/test_api.py` 末尾に追加:

```python
def test_backtest_accepts_exit_params(client):
    r = client.post("/backtest", json={"demo": True, "days": 40, "exit_mode": "plan",
                                       "trail_atr_mult": 3.0, "max_hold_days": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["exit_mode"] == "plan"
    assert "trail_exit_count" in body and "time_exit_count" in body


def test_backtest_default_has_no_trail_or_time_exits(client):
    r = client.post("/backtest", json={"demo": True, "days": 40, "exit_mode": "plan"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("trail_exit_count", 0) == 0
    assert body.get("time_exit_count", 0) == 0
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_backtest_accepts_exit_params -q`
Expected: FAIL（`trail_exit_count` が body に無い＝デフォルトの BacktestIn は未対応で run_backtest に届かない）

- [ ] **Step 3: 実装する**

(a) `BacktestIn`（行109-116）に2項目追加:

```python
class BacktestIn(BaseModel):
    tickers: Optional[list[str]] = None
    demo: bool = False
    days: Optional[int] = None   # 未指定なら取得期間全体（warmup以降）を評価
    initial_capital: float = 3000.0
    exit_mode: str = "plan"      # 既定を plan（検証=提示）に
    persist: bool = False
    period: str = "3y"
    trail_atr_mult: float = 0.0  # >0 でトレーリング（plan モード）。0=OFF
    max_hold_days: int = 0       # >0 で時間切れ手仕舞い。0=OFF
```

(b) `/backtest` ハンドラの `run_backtest` 呼び出し（行548-551）の直前にクランプを追加し、呼び出しに貫通:

```python
    trail = max(0.0, float(payload.trail_atr_mult or 0.0))   # 負値・不正は OFF へ
    mhd = max(0, int(payload.max_hold_days or 0))
    result = run_backtest(histories, configs=common, initial_capital=payload.initial_capital,
                          backtest_days=bdays, buy_threshold=buy_th, sell_threshold=sell_th,
                          exit_mode=payload.exit_mode, cost=cost, regime_series=rs,
                          index_history=idx_df, rs_params=rs_params, risk_pct=_risk_pct(),
                          trail_atr_mult=trail, max_hold_days=mhd)
```

(c) `OptimizeIn`（行568-573）に2項目追加:

```python
class OptimizeIn(BaseModel):
    tickers: Optional[list[str]] = None
    demo: bool = False
    initial_capital: float = 3000.0
    period: str = "3y"
    split_ratio: float = 0.7
    trail_atr_mult: float = 0.0
    max_hold_days: int = 0
```

(d) `/optimize` ハンドラの `evaluate_holdout` 呼び出し（行592-594）の直前にクランプを追加し、貫通:

```python
    trail = max(0.0, float(payload.trail_atr_mult or 0.0))
    mhd = max(0, int(payload.max_hold_days or 0))
    res = evaluate_holdout(histories, common, split_ratio=payload.split_ratio, cost=cost,
                           initial_capital=payload.initial_capital, regime_series=rs,
                           index_history=idx_df, rs_params=rs_params, risk_pct=_risk_pct(),
                           trail_atr_mult=trail, max_hold_days=mhd)
```

- [ ] **Step 4: テスト緑＋backend 全体を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全 PASS（新規2件含む・config 件数テスト `== 14` も不変）

- [ ] **Step 5: commit**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: /backtest・/optimize に trail/time 出口パラメータを配線（打ち手9）"
```

---

## Task 6: フロント（純関数＋入力＋表示）

**Files:**
- Create: `frontend/src/lib/backtest.ts`（純関数 `buildBacktestBody`）
- Create: `frontend/src/lib/__tests__/backtest.test.ts`
- Modify: `frontend/src/lib/api.ts`（`api.backtest` body 型 行197・`BacktestResult` 行122-146）
- Modify: `frontend/src/app/simulation/page.tsx`（state・入力・表示・`buildBacktestBody` 利用）

- [ ] **Step 1: 純関数のテストを書く**

`frontend/src/lib/__tests__/backtest.test.ts` を作成:

```typescript
import { describe, it, expect } from "vitest";
import { buildBacktestBody } from "@/lib/backtest";

const base = { capital: 3000, days: 22, demo: true, persist: false,
               atrExit: true, trailAtrMult: 0, maxHoldDays: 0 };

describe("buildBacktestBody", () => {
  it("trail/time が 0 ならペイロードに含めない（OFF）", () => {
    const b = buildBacktestBody(base);
    expect(b.exit_mode).toBe("plan");
    expect(b.trail_atr_mult).toBeUndefined();
    expect(b.max_hold_days).toBeUndefined();
  });
  it("trail/time が正なら貫通する", () => {
    const b = buildBacktestBody({ ...base, trailAtrMult: 3, maxHoldDays: 10 });
    expect(b.trail_atr_mult).toBe(3);
    expect(b.max_hold_days).toBe(10);
  });
  it("atrExit=false は score モード", () => {
    expect(buildBacktestBody({ ...base, atrExit: false }).exit_mode).toBe("score");
  });
});
```

- [ ] **Step 2: 失敗を確認**

Run: `npm --prefix frontend test -- --run src/lib/__tests__/backtest.test.ts`
Expected: FAIL（`buildBacktestBody` が存在しない）

- [ ] **Step 3: 純関数を実装**

`frontend/src/lib/backtest.ts` を作成:

```typescript
export type BacktestForm = {
  capital: number;
  days: number;
  demo: boolean;
  persist: boolean;
  atrExit: boolean;
  trailAtrMult: number;
  maxHoldDays: number;
};

export type BacktestBody = {
  initial_capital: number;
  days: number;
  demo: boolean;
  persist: boolean;
  exit_mode: "score" | "plan";
  trail_atr_mult?: number;
  max_hold_days?: number;
};

/** フォーム状態から /backtest のリクエストボディを組み立てる純関数。
 *  trail/time は 0（OFF）のとき省略し、現挙動と同じペイロードにする。 */
export function buildBacktestBody(f: BacktestForm): BacktestBody {
  const body: BacktestBody = {
    initial_capital: f.capital,
    days: f.days,
    demo: f.demo,
    persist: f.persist,
    exit_mode: f.atrExit ? "plan" : "score",
  };
  if (f.trailAtrMult > 0) body.trail_atr_mult = f.trailAtrMult;
  if (f.maxHoldDays > 0) body.max_hold_days = f.maxHoldDays;
  return body;
}
```

- [ ] **Step 4: テスト緑を確認**

Run: `npm --prefix frontend test -- --run src/lib/__tests__/backtest.test.ts`
Expected: 3 passed

- [ ] **Step 5: api.ts の型を更新**

`frontend/src/lib/api.ts`:
- `BacktestResult`（行122-146）の `signal_exit_count?` の下に追加:

```typescript
  trail_exit_count?: number;
  time_exit_count?: number;
```

- `api.backtest` の body 型（行197）に trail/time を許可:

```typescript
  backtest: (body: { tickers?: string[]; initial_capital?: number; days?: number; demo?: boolean; persist?: boolean; exit_mode?: "score" | "plan"; period?: string; trail_atr_mult?: number; max_hold_days?: number }) =>
    req<BacktestResult>("/backtest", { method: "POST", body: JSON.stringify(body) }),
```

- [ ] **Step 6: simulation 画面を更新**

`frontend/src/app/simulation/page.tsx`:
- 先頭の import に追加: `import { buildBacktestBody } from "@/lib/backtest";`
- state 追加（行22 の `atrExit` の下）:

```typescript
  const [trailAtrMult, setTrailAtrMult] = useState(0);
  const [maxHoldDays, setMaxHoldDays] = useState(0);
```

- `run()` のボディ構築（行31-34）を純関数に置換:

```typescript
      const r = await api.backtest(
        buildBacktestBody({ capital, days, demo, persist, atrExit, trailAtrMult, maxHoldDays }),
      );
```

- ATR出口チェックボックス（行75-78）の下に、`atrExit` のとき表示する任意入力2つを追加:

```tsx
          {atrExit && (
            <>
              <label className="flex flex-col gap-1">
                トレーリングATR倍率（0=OFF）
                <input
                  type="number"
                  step="0.5"
                  value={trailAtrMult}
                  onChange={(e) => setTrailAtrMult(Number(e.target.value))}
                  className="w-40 rounded border px-2 py-1"
                />
              </label>
              <label className="flex flex-col gap-1">
                最大保有日数（0=OFF）
                <input
                  type="number"
                  value={maxHoldDays}
                  onChange={(e) => setMaxHoldDays(Number(e.target.value))}
                  className="w-40 rounded border px-2 py-1"
                />
              </label>
            </>
          )}
```

- 「ATR出口の内訳」セクション（行118-129）の Metric 群に2つ追加（`signal_exit_count` の Metric の下）:

```tsx
                <Metric label="トレーリングで決済" value={`${result.trail_exit_count ?? 0} 回`} />
                <Metric label="時間切れで決済" value={`${result.time_exit_count ?? 0} 回`} />
```

- [ ] **Step 7: frontend 全体が緑を確認**

Run: `npm --prefix frontend test`
Expected: 既存16 + 新規3 = 19 passed

- [ ] **Step 8: ビルド型チェック（任意・推奨）**

Run: `npm --prefix frontend run build`（または lint）
Expected: 型エラーなし

- [ ] **Step 9: commit**

```bash
git add frontend/src/lib/backtest.ts frontend/src/lib/__tests__/backtest.test.ts frontend/src/lib/api.ts frontend/src/app/simulation/page.tsx
git commit -m "feat: バックテスト画面にトレーリング/最大保有日数の入力と内訳表示を追加（打ち手9）"
```

---

## 完了条件（全タスク後の最終確認）

- [ ] `backend/venv/bin/python -m pytest backend/ -q` が全 PASS（既存122 + 新規約10 = 約132）。
- [ ] `npm --prefix frontend test` が全 PASS（既存16 + 新規3 = 19）。
- [ ] 既定OFF（trail_atr_mult=0・max_hold_days=0）で plan-mode の `closed_pnls`・約定価格が現挙動と一致（Task2 回帰テストで担保）。
- [ ] `DEFAULT_CONFIGS` 件数は 14 のまま（`test_api.py` の `== 14` 不変）。
- [ ] spec の非スコープ（分割利確・R:R動的化・ライブ作戦への採用）に手を出していない。

## レビュー観点（最終 code-review 用）

- トレーリングON時に固定 target が確実に無効化され、`take_profit_count`/`stop_loss_count` が trailing ラン中 0 になる（spec §10 排他性）。
- look-ahead: `cur_stop` は更新前の `high_water`（前バーまで）で算出されている。
- 全 `run_backtest`/`evaluate_holdout` 呼び出し経路で trail/time が既定OFF（benchmark の score モードには貫通しない）。
- フロントのペイロードは trail/time=0 で省略され、現挙動と同一。
