# 地合いレジームゲート（打ち手3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 指数から地合いレジーム（risk_on/neutral/risk_off）を判定し、`evaluate()` の一次ゲート（リスクオフ×新規BUYはペナルティ減点）として効かせる。検証=提示を保ち、バックテストにも反映する。

**Architecture:** `signals.market_regime`（指数→レジーム）と `regime_series`（日次レジームを前計算）を新設。`evaluate` に任意 `regime` を足し `market_regime` 設定ルールでゲート。バックテスト層は `regime_series` を受け取り各営業日 `asof` 参照。すべて任意引数で後方互換（未指定なら従来挙動）。

**Tech Stack:** Python 3.13 / pandas / pandas-ta / FastAPI / pytest。フラットモジュール。テストは `backend/venv/bin/python -m pytest`、ネット非依存（demo/合成データ）。

**設計書:** `docs/superpowers/specs/2026-06-18-market-regime-gate-design.md`

---

## File Structure

| ファイル | 区分 | 責務 |
|---|---|---|
| `backend/signals.py` | 改修 | `market_regime`・`regime_series` 新設、`evaluate(regime=)` ゲート、`DEFAULT_CONFIGS` に `market_regime` 追加 |
| `backend/test_signals.py` | 改修 | レジーム判定・系列・ゲートのテスト |
| `backend/backtest.py` | 改修 | `run_backtest`/`_run_backtest_plan` に `regime_series`、`_regime_at` で各日 asof 参照 |
| `backend/test_backtest.py` | 改修 | バックテストのレジーム抑制・asof のテスト |
| `backend/evaluation.py` | 改修 | `benchmark`/`evaluate_holdout` に `regime_series` を透過 |
| `backend/test_evaluation.py` | 改修 | holdout が `regime_series` を受ける |
| `backend/main.py` | 改修 | `perform_refresh` のレジーム配線、`/backtest`・`/optimize` で指数取得→系列前計算 |
| `backend/test_api.py` | 改修 | `/backtest`・`/optimize` の結合（レジーム込み） |

各タスク TDD（失敗テスト→失敗確認→実装→成功確認→コミット）。DRY・YAGNI。

---

## Task 1: signals — market_regime / regime_series / evaluate ゲート

**Files:**
- Modify: `backend/signals.py`（`weekly_trend` の後ろに2関数追加・`evaluate` の `weekly_trend_filter` ブロック直後にゲート追加・`DEFAULT_CONFIGS` にルール追加）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_signals.py`（先頭の import に `numpy as np`・`pandas as pd`・`market_regime, regime_series` が無ければ追加）

```python
import numpy as np
import pandas as pd
from signals import market_regime, regime_series   # 既存 import 群に追加


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
    assert market_regime(_idx(closes)) == "risk_off"   # 直近高値1300→1100 ≈15%下落


def test_market_regime_short_series_is_neutral():
    assert market_regime(_idx([1000, 1010, 1005])) == "neutral"


def test_regime_series_is_causal_and_aligned():
    df = _idx(np.linspace(1000, 1300, 60))
    s = regime_series(df)
    assert len(s) == len(df)
    for i in (10, 30, 59):   # 各日の値はその日までのデータの market_regime と一致
        assert s.iloc[i] == market_regime(df.iloc[:i + 1])


def test_evaluate_regime_records_and_no_change_when_not_risk_off():
    from signals import evaluate, DEFAULT_CONFIGS
    from market import synthetic_history
    df = synthetic_history("X.T", seed=3)
    base = evaluate(df, DEFAULT_CONFIGS, 2, -2)
    on = evaluate(df, DEFAULT_CONFIGS, 2, -2, regime="risk_on")
    assert on[2]["regime"] == "risk_on"
    assert (on[0], on[1]) == (base[0], base[1])   # risk_on は記録のみ・不変


def test_evaluate_regime_off_penalizes_a_buy_signal():
    from signals import evaluate, DEFAULT_CONFIGS
    from market import synthetic_history
    for seed in range(20):   # buy が出る合成データを決定論的に探す
        df = synthetic_history("X.T", seed=seed)
        bs, bd, _ = evaluate(df, DEFAULT_CONFIGS, 2, -2)
        if bd == "buy":
            os_, od, odt = evaluate(df, DEFAULT_CONFIGS, 2, -2, regime="risk_off")
            assert os_ == bs - 2                      # penalty=2 減点
            assert odt["regime_filter"] == -2
            return
    raise AssertionError("buy シグナルの合成データが見つからない（前提を見直す）")
```

- [ ] **Step 2: 失敗確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -k "regime" -v`
Expected: FAIL（`cannot import name 'market_regime'`）

- [ ] **Step 3: 実装** — `backend/signals.py`

`weekly_trend`（既存）の直後に2関数を追加：
```python
def market_regime(index_df, *, sma: int = 13, dd_lookback: int = 60,
                  dd_threshold: float = 0.10) -> str:
    """指数 OHLCV の最終行時点の地合いレジームを返す: 'risk_on'|'neutral'|'risk_off'。

    呼び出し側が index_df を日付で切ることで look-ahead を回避する。
    """
    if index_df is None or len(index_df) < 5:
        return "neutral"
    trend = weekly_trend(index_df, sma)                       # 既存（up/down/flat）
    closes = index_df["close"].tail(dd_lookback)
    peak = float(closes.max())
    last = float(closes.iloc[-1])
    dd = (peak - last) / peak if peak > 0 else 0.0            # 直近高値からの下落率
    if trend == "down" or dd >= dd_threshold:
        return "risk_off"
    if trend == "up" and dd < dd_threshold / 2:
        return "risk_on"
    return "neutral"


def regime_series(index_df, **params) -> "pd.Series":
    """各営業日について「その日までの指数」でのレジームを前計算（look-ahead 安全）。

    バックテストで1回だけ前計算し、各営業日 asof で参照する。
    """
    idx = index_df.sort_index()
    return pd.Series({idx.index[i]: market_regime(idx.iloc[:i + 1], **params)
                      for i in range(len(idx))})
```

`evaluate` の `weekly_trend_filter` ブロック（`return score, direction, detail` の直前）にゲートを追加：
```python
    # --- 地合いレジームの一次ゲート（指数版の足切り） ---
    if regime is not None:
        detail["regime"] = regime
        rf = _find_cfg(configs, "market_regime")
        if rf is not None and regime == "risk_off" and direction == "buy":
            mode = rf["params"].get("mode", "penalty") if "params" in rf else rf.get("mode", "penalty")
            penalty = int(rf["params"].get("penalty", 2)) if "params" in rf else int(rf.get("penalty", 2))
            if mode == "block":
                detail["regime_filter"] = "blocked"
                direction = "neutral"
            else:
                score -= penalty
                detail["regime_filter"] = -penalty
                direction = _direction(score)

    return score, direction, detail
```
`evaluate` のシグネチャに `regime` を追加：
```python
def evaluate(df, configs=None, buy_threshold=BUY_THRESHOLD, sell_threshold=SELL_THRESHOLD,
             regime: str | None = None):
```
> 注：`_find_cfg` は設定 dict をそのまま返す。DEFAULT_CONFIGS のルールは `{"rule_type":..., "params": {...}}` 形、DB 由来も `params` キーを持つため `rf["params"]` で読める。上の三項は両形に対応（保険）。

`DEFAULT_CONFIGS` リストに次の1行を追加（`weekly_trend_filter` の行の近く）：
```python
    {"rule_type": "market_regime",
     "params": {"mode": "penalty", "penalty": 2, "sma": 13, "dd_lookback": 60, "dd_threshold": 0.10},
     "weight": 1, "enabled": 1},
```

- [ ] **Step 4: 成功確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q`
Expected: PASS（既存＋regime テスト）

- [ ] **Step 5: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: 地合いレジーム判定とevaluateの一次ゲート（market_regime/regime_series）"
```

---

## Task 2: backtest — regime_series 配線（asof 参照）

**Files:**
- Modify: `backend/backtest.py`（`run_backtest` @20・score 評価 @59,@100・`_run_backtest_plan` @124・plan 評価 @190,@215）
- Test: `backend/test_backtest.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_backtest.py`

```python
def test_regime_at_is_asof():
    from backtest import _regime_at
    s = pd.Series({pd.Timestamp("2026-01-05"): "risk_on",
                   pd.Timestamp("2026-01-10"): "risk_off"})
    assert _regime_at(s, pd.Timestamp("2026-01-07")) == "risk_on"   # 直近以前
    assert _regime_at(s, pd.Timestamp("2026-01-10")) == "risk_off"
    assert _regime_at(s, pd.Timestamp("2026-01-01")) is None        # 系列開始前 → None
    assert _regime_at(None, pd.Timestamp("2026-01-07")) is None


def test_run_backtest_regime_series_suppresses_risk_off_buys():
    from signals import regime_series
    stock = _trend_up_df(n=120, seed=5)
    idx_close = np.linspace(1300, 1000, 120)            # 同期間の下降指数 → ほぼ risk_off
    index_df = pd.DataFrame({"open": idx_close, "high": idx_close, "low": idx_close,
                             "close": idx_close, "volume": np.full(120, 1e6)},
                            index=stock.index)
    rs = regime_series(index_df)
    without = run_backtest({"X.T": stock}, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60)
    withr = run_backtest({"X.T": stock}, configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=60,
                         regime_series=rs)
    # レジームゲートはBUYを抑制するのみ＝約定数は増えない
    assert withr["trade_count"] <= without["trade_count"]
```

- [ ] **Step 2: 失敗確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k regime -v`
Expected: FAIL（`cannot import name '_regime_at'` / `regime_series` 引数なし）

- [ ] **Step 3: 実装** — `backend/backtest.py`

モジュールに helper を追加（`run_backtest` の前など）：
```python
def _regime_at(regime_series, d):
    """日次レジーム系列から d 時点（以前）のレジームを返す。None/未来日は None。"""
    if regime_series is None:
        return None
    v = regime_series.asof(d)
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v
```

`run_backtest` のシグネチャに `regime_series=None` を追加。dispatch で `_run_backtest_plan(..., regime_series)` を渡す。
score モードの2つの `evaluate` 呼び出しを更新：
- 意思決定（旧59行）：`evaluate(window, configs, buy_threshold, sell_threshold, regime=_regime_at(regime_series, d))`
- signal_rows（旧100行）：`evaluate(df, configs, buy_threshold, sell_threshold, regime=_regime_at(regime_series, df.index[-1]))`

`_run_backtest_plan` のシグネチャ末尾に `regime_series=None` を追加。plan モードの2つの `evaluate` 呼び出しを更新：
- 意思決定（旧190行・`window = df.iloc[:i+1]`）：`regime=_regime_at(regime_series, df.index[i])` を渡す
- signal_rows（旧215行）：`regime=_regime_at(regime_series, df.index[-1])` を渡す

- [ ] **Step 4: 成功確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q`
Expected: PASS（既存＋regime 2件）

- [ ] **Step 5: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: バックテストに地合いレジーム系列を配線（各営業日asof・検証=提示）"
```

---

## Task 3: evaluation — benchmark / evaluate_holdout に regime_series 透過

**Files:**
- Modify: `backend/evaluation.py`（`benchmark` @37・`run_backtest` 呼び @57・`evaluate_holdout` @75・`_bt` @91）
- Test: `backend/test_evaluation.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_evaluation.py`

```python
def test_evaluate_holdout_accepts_regime_series():
    from signals import regime_series
    hist = {"X.T": _df(n=200)}
    idx_close = np.linspace(1300, 1000, 200)
    index_df = pd.DataFrame({"open": idx_close, "high": idx_close, "low": idx_close,
                             "close": idx_close, "volume": np.full(200, 1e6)},
                            index=hist["X.T"].index)
    rs = regime_series(index_df)
    res = evaluate_holdout(hist, DEFAULT_CONFIGS, split_ratio=0.7, regime_series=rs,
                           initial_capital=3000.0, warmup_days=35)
    assert res["out_of_sample"]["sample"] == "out_of_sample"
    assert "benchmark" in res
```

- [ ] **Step 2: 失敗確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -k regime -v`
Expected: FAIL（`evaluate_holdout` に `regime_series` 引数なし）

- [ ] **Step 3: 実装** — `backend/evaluation.py`

`benchmark(...)` のシグネチャに `regime_series=None` を追加し、内部の `run_backtest(... exit_mode="score", cost=cost)` 呼び出しに `regime_series=regime_series` を渡す。

`evaluate_holdout(...)` のシグネチャに `regime_series=None` を追加。`_bt` helper の `run_backtest(...)` 呼び出しに `regime_series=regime_series` を渡す。`benchmark(...)` 呼び出しにも `regime_series=regime_series` を渡す。

- [ ] **Step 4: 成功確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: 評価層(benchmark/evaluate_holdout)に地合いレジーム系列を透過"
```

---

## Task 4: main — perform_refresh のライブ配線・エンドポイントで指数取得

**Files:**
- Modify: `backend/main.py`（import・`perform_refresh` @312-356・`/backtest` @429-467・`/optimize` @485-）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗テストを追記** — `backend/test_api.py`

```python
def test_backtest_signals_include_regime(client):
    r = client.post("/backtest", json={"demo": True, "days": 60, "exit_mode": "plan"})
    assert r.status_code == 200
    res = r.json()
    # 指数（demo合成）からレジームが算出され、最終シグナルの detail に regime が入る
    assert res["signals"] and "regime" in res["signals"][0]["detail"]


def test_optimize_still_ok_with_regime(client):
    r = client.post("/optimize", json={"demo": True, "split_ratio": 0.7})
    assert r.status_code == 200
    assert r.json()["out_of_sample"]["sample"] == "out_of_sample"
```

- [ ] **Step 2: 失敗確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k "regime or optimize_still" -v`
Expected: FAIL（detail に regime 無し）

- [ ] **Step 3: 実装** — `backend/main.py`

import に追加（既存 `from signals import ...` 群へ）：`market_regime, regime_series` と `_find_cfg`（無ければ）。`import os` は既存。

共通 helper を追加（モジュール内）：
```python
def _regime_params(common: list[dict]) -> dict:
    rp = _find_cfg(common, "market_regime")
    p = (rp.get("params", {}) if rp else {}) or {}
    return {k: p[k] for k in ("sma", "dd_lookback", "dd_threshold") if k in p}


def _fetch_regime_series(period: str, demo: bool, common: list[dict]):
    """指数を取得し日次レジーム系列を返す（取得失敗・空は None）。"""
    try:
        idx = get_history(os.environ.get("GEMINI_INDEX_TICKER", "^N225"),
                          period=period, demo=demo)
        if idx.empty:
            return None
        return regime_series(idx, **_regime_params(common))
    except Exception:
        return None
```

`perform_refresh`（@312 付近）：`index_trend` 算出ブロックでレジームも算出し、`evaluate` と `market_ctx` に渡す。
```python
    index_trend = None
    regime = None
    try:
        idx_df = get_history(index_ticker, period=period, demo=demo)
        if not idx_df.empty:
            index_trend = weekly_trend(idx_df)
            regime = market_regime(idx_df, **_regime_params(common))
    except Exception:
        index_trend = None
```
評価呼び出し（@332）：`score, direction, detail = evaluate(df, ticker_cfgs, buy_th, sell_th, regime=regime)`
AI市況（@356 の market_ctx dict）：`"regime": regime` を追加。

`/backtest`（@455-463）：`common` を使って系列を作り run_backtest と benchmark に渡す。
```python
    rs = _fetch_regime_series(payload.period, payload.demo, common)
    result = run_backtest(histories, configs=common, ..., cost=cost, regime_series=rs)
    ...
    result["benchmark"] = benchmark(histories, common, ..., cost=cost, regime_series=rs)
```

`/optimize`（@498 付近）：
```python
    rs = _fetch_regime_series(payload.period, payload.demo, common)
    res = evaluate_holdout(histories, common, split_ratio=payload.split_ratio, cost=cost,
                           initial_capital=payload.initial_capital, regime_series=rs)
```

- [ ] **Step 4: 全体確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全件 PASS（既存70＋本機能の追加分）

- [ ] **Step 5: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: perform_refresh と /backtest・/optimize に地合いレジームを配線"
```

---

## 完了の定義
- 指数から `market_regime` が3段階を返し、`evaluate` のゲートで**リスクオフ時に新規BUYが減点**される。
- `regime`/`regime_series` 未指定の既存経路は**挙動不変**（既存70件グリーン）。
- バックテスト（`/optimize` OOS）がレジーム込みで動く＝**検証=提示**維持・look-ahead無し（`asof`）。
- `/backtest` の signals に `detail.regime` が出る。
- `backend/ pytest` 全件グリーン。

## スコープ外（将来）
- レジーム別の順張り/逆張り重み切替（打ち手5）、連続確信度・ランキング（打ち手6）、フロントの地合い表示、SELL側の対称ゲート。

> ⚠️ 本計画は地合いを判断に加えるもので利益を保証しない。投資は自己責任の原則は不変。
