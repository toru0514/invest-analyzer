# 打ち手10: 決算日カレンダー＋決算跨ぎ回避 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 決算日カレンダーを取得し、作戦カードに「N日後に決算」警告を出し、バックテストで決算ギャップ再現＋跨ぎ回避を比較できるようにする（既定 OFF＝現挙動完全再現）。

**Architecture:** 加法的・後方互換（打ち手6〜9 と同方針）。決算日取得は `market.fetch_earnings_dates`（yfinance best-effort）。ライブは `daily_plan.days_to_earnings` を永続化しフロントの純関数でしきい値判定して琥珀バッジ表示（score/sizing/direction 不変）。バックテストは plan モード `_run_backtest_plan` のみに「決算翌日の窓を寄りfill」＋「N営業日前手仕舞い」を閉じ込め、`BacktestIn`/`OptimizeIn` のペイロード `earnings_aware`/`earnings_exit_days` で供給する。

**Tech Stack:** Python / FastAPI / pandas / pytest（backend, 現状133件）、Next.js / TypeScript / vitest（frontend, 現状19件）。

**Spec:** `docs/superpowers/specs/2026-06-22-earnings-calendar-avoidance-design.md`

**前提メモリ:** [[roadmap-progress]]・[[signal-tests-prefer-deterministic-ohlc]]

**テスト実行:** backend `backend/venv/bin/python -m pytest backend/ -q` ／ frontend `npm --prefix frontend test`

---

## Task 1: `market.fetch_earnings_dates`（決算日付の取得・正規化）

**Files:**
- Modify: `backend/market.py`（末尾に関数追加）
- Test: `backend/test_market.py`（新規）

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_market.py` を新規作成:

```python
"""market.py のネット非依存テスト（yfinance はスタブする）。"""
import pandas as pd
import pytest

import market


def test_fetch_earnings_dates_normalizes_and_sorts(monkeypatch):
    """tz-aware・降順の earnings index を tz-naive・midnight・昇順のリストに正規化する。"""
    class FakeTicker:
        def __init__(self, t):
            pass
        def get_earnings_dates(self, limit=12):
            idx = pd.DatetimeIndex([
                pd.Timestamp("2026-08-05 16:00", tz="America/New_York"),
                pd.Timestamp("2026-05-07 16:00", tz="America/New_York"),
            ])
            return pd.DataFrame({"EPS Estimate": [1.0, 0.9]}, index=idx)

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)
    out = market.fetch_earnings_dates("X.T")
    assert out == [pd.Timestamp("2026-05-07"), pd.Timestamp("2026-08-05")]


def test_fetch_earnings_dates_handles_missing(monkeypatch):
    """例外・空 DataFrame はどちらも None（best-effort・例外を投げない契約）。"""
    class Boom:
        def __init__(self, t):
            pass
        def get_earnings_dates(self, limit=12):
            raise RuntimeError("no data for JP ticker")

    monkeypatch.setattr("yfinance.Ticker", Boom)
    assert market.fetch_earnings_dates("X.T") is None

    class Empty:
        def __init__(self, t):
            pass
        def get_earnings_dates(self, limit=12):
            return pd.DataFrame()

    monkeypatch.setattr("yfinance.Ticker", Empty)
    assert market.fetch_earnings_dates("X.T") is None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_market.py -q`
Expected: FAIL（`AttributeError: module 'market' has no attribute 'fetch_earnings_dates'`）

- [ ] **Step 3: 最小実装**

`backend/market.py` の末尾（`fetch_earnings_days` の後）に追加:

```python
def fetch_earnings_dates(ticker: str, limit: int = 12) -> list[pd.Timestamp] | None:
    """過去＋将来の決算日（tz-naive・midnight・昇順）。取得不可・無しは None（best-effort）。

    yfinance の決算日 index は tz-aware（取引所TZ）なことが多い。tz を落として
    日付化し、バックテストの df.index（tz-naive・get_history 由来）と素直に比較できるようにする。
    例外・空は None（fetch_earnings_days と同じ堅牢契約・例外は投げない）。
    """
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
        if df is None or df.empty:
            return None
        idx = pd.to_datetime(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        dates = sorted({ts.normalize() for ts in idx})
        return dates or None
    except Exception:
        return None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_market.py -q`
Expected: PASS（2 件）

- [ ] **Step 5: コミット**

```bash
git add backend/market.py backend/test_market.py
git commit -m "feat: market.fetch_earnings_dates（決算日付の取得・正規化・打ち手10）"
```

---

## Task 2: `daily_plan.days_to_earnings` 列（永続化の器）

**Files:**
- Modify: `backend/db.py`（SCHEMA・`_migrate_daily_plan`・`upsert_plan`）
- Test: `backend/test_api.py`（移行テストを1件追加）

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_daily_plan_sizing_columns_and_upsert`（行299付近）の直後に追加:

```python
def test_daily_plan_earnings_column_migration(tmp_path, monkeypatch):
    """既存 daily_plan に days_to_earnings が無くても冪等マイグレーションで追加され、upsert で値が入る。"""
    import sqlite3
    import db as dbmod
    dbfile = tmp_path / "old_earn.db"
    conn = sqlite3.connect(dbfile)
    # days_to_earnings の無い旧スキーマ（打ち手8 相当）
    conn.execute(
        "CREATE TABLE daily_plan (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT NOT NULL, plan_date TEXT NOT NULL, direction TEXT, score INTEGER, "
        "vol_ratio REAL, weekly_trend TEXT, limit_price REAL, stop_price REAL, "
        "target_price REAL, rationale TEXT, confidence REAL, shares REAL, risk_amount REAL, "
        "ai_summary TEXT, ai_confidence INTEGER, ai_risks TEXT, created_at TEXT, "
        "UNIQUE(ticker, plan_date))")
    conn.commit(); conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()
    with dbmod.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_plan)")}
    assert "days_to_earnings" in cols
    dbmod.upsert_plan({"ticker": "E.T", "plan_date": "2026-06-01", "direction": "buy",
                       "score": 3, "vol_ratio": None, "weekly_trend": None,
                       "limit_price": 1000.0, "stop_price": 950.0, "target_price": 1100.0,
                       "rationale": "x", "days_to_earnings": 3})
    row = [r for r in dbmod.list_plan("2026-06-01") if r["ticker"] == "E.T"][0]
    assert row["days_to_earnings"] == 3
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_daily_plan_earnings_column_migration -q`
Expected: FAIL（`days_to_earnings` 列が無い／upsert に列が無い）

- [ ] **Step 3: 実装**

`backend/db.py` で3箇所:

1) `SCHEMA` の `CREATE TABLE daily_plan`（`risk_amount REAL,` の次の行）に追加:
```sql
  days_to_earnings INTEGER,
```

2) `_migrate_daily_plan`（行138付近）の後付け列タプルに追加:
```python
    for col, decl in (("ai_summary", "TEXT"), ("ai_confidence", "INTEGER"),
                      ("ai_risks", "TEXT"), ("confidence", "REAL"),
                      ("shares", "REAL"), ("risk_amount", "REAL"),
                      ("days_to_earnings", "INTEGER")):
```

3) `upsert_plan`（行413付近）の `setdefault` ループ・INSERT 列・VALUES・ON CONFLICT に `days_to_earnings` を追加:
```python
    for k in ("ai_summary", "ai_confidence", "ai_risks", "confidence", "shares",
              "risk_amount", "days_to_earnings"):
        row.setdefault(k, None)
```
INSERT 文の列リストに `days_to_earnings`、VALUES に `:days_to_earnings`、`ON CONFLICT ... DO UPDATE SET` に `days_to_earnings=excluded.days_to_earnings,` を追加（既存 `risk_amount` の隣に並べる）。

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_daily_plan_earnings_column_migration -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/db.py backend/test_api.py
git commit -m "feat: daily_plan.days_to_earnings 列＋冪等マイグレーション（打ち手10）"
```

---

## Task 3: `perform_refresh` が `days_to_earnings` を永続化

**Files:**
- Modify: `backend/main.py`（`upsert_plan({...})` の dict・行447付近）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_perform_refresh_sizes_buy_end_to_end`（行353付近）の直後に追加:

```python
def test_perform_refresh_persists_days_to_earnings(client, monkeypatch):
    """非 demo 経路で perform_refresh が fetch_earnings_days の結果を daily_plan に永続化する。"""
    import numpy as np
    import pandas as pd
    import main
    closes = np.linspace(1000.0, 1300.0, 120)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=len(closes))
    up = pd.DataFrame({"open": closes - 3.0, "high": closes + 10.0, "low": closes - 10.0,
                       "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)
    monkeypatch.setattr(main, "get_history", lambda *a, **k: up.copy())
    monkeypatch.setattr(main, "fetch_earnings_days", lambda t: 3)

    client.post("/watchlist", json={"ticker": "EARN.T", "name": "決算銘柄"})
    try:
        client.post("/refresh")   # demo=False → fetch_earnings_days 経路を通す
        rows = client.get("/plan").json()["rows"]
        row = [r for r in rows if r["ticker"] == "EARN.T"][0]
        assert row["days_to_earnings"] == 3
    finally:
        for w in client.get("/watchlist").json():
            if w["ticker"] == "EARN.T":
                client.delete(f"/watchlist/{w['id']}")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_perform_refresh_persists_days_to_earnings -q`
Expected: FAIL（`days_to_earnings` が None＝永続化されていない）

- [ ] **Step 3: 実装**

`backend/main.py` の `db.upsert_plan({...})`（行447付近）の dict に1行追加（`"risk_amount": plan_risk,` の隣）:
```python
            "days_to_earnings": days_to_earnings,
```
（`days_to_earnings` は行436で算出済み。AI解説への受け渡しは不変。）

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_perform_refresh_persists_days_to_earnings -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: perform_refresh が days_to_earnings を daily_plan に永続化（打ち手10）"
```

---

## Task 4: バックテストの決算ギャップ再現（窓の寄りfill）＋配線＋既定OFF回帰

**Files:**
- Modify: `backend/backtest.py`（`run_backtest` 行39・`_run_backtest_plan` 行152）
- Test: `backend/test_backtest.py`

決算ロジックは backtest 層では **`earnings_map`（None=OFF）** で供給する（`earnings_aware` bool は API 層の概念で Task 7）。

- [ ] **Step 1: 失敗するテスト（既定OFF回帰＋窓fill）を書く**

`backend/test_backtest.py` の末尾（time_exit テストの後）に追加:

```python
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k earnings -q`
Expected: FAIL（`run_backtest() got an unexpected keyword argument 'earnings_map'`）

- [ ] **Step 3: 実装**

`backend/backtest.py`:

(a) `run_backtest`（行39 シグネチャ）に追加し、plan ディスパッチ（行55-61）へ貫通:
```python
def run_backtest(
    histories, configs=None, initial_capital=INITIAL_CAPITAL,
    backtest_days=BACKTEST_DAYS, warmup_days=WARMUP_DAYS,
    buy_threshold=BUY_THRESHOLD, sell_threshold=SELL_THRESHOLD,
    exit_mode="score", cost=None, eval_start_date=None, regime_series=None,
    index_history=None, rs_params=None, risk_pct=DEFAULT_RISK_PCT,
    trail_atr_mult=0.0, max_hold_days=0,
    earnings_map=None, earnings_exit_days=0,
) -> dict:
```
plan ディスパッチの `_run_backtest_plan(...)` 呼び出し（行56-61）末尾に:
```python
                                  trail_atr_mult=trail_atr_mult, max_hold_days=max_hold_days,
                                  earnings_map=earnings_map, earnings_exit_days=earnings_exit_days)
```

(b) `_run_backtest_plan`（行152 シグネチャ）に追加:
```python
def _run_backtest_plan(histories, configs, initial_capital, backtest_days,
                       warmup_days, buy_threshold, sell_threshold, cost,
                       eval_start_date, regime_series=None,
                       index_history=None, rs_params=None,
                       risk_pct=DEFAULT_RISK_PCT,
                       trail_atr_mult=0.0, max_hold_days=0,
                       earnings_map=None, earnings_exit_days=0) -> dict:
```

(c) 銘柄ループ先頭（`df = df.sort_index()` の直後・行173付近）に決算位置を計算:
```python
        # 決算翌日の窓バー位置（E より厳密に後の最初のバー）。earnings_map 無し→空＝OFF。
        edates = (earnings_map or {}).get(ticker) or []
        gaps_all = sorted({int(df.index.searchsorted(e, side="right")) for e in edates})
        gaps_in = {g for g in gaps_all if g < len(df)}
```

(d) バー読み取り（行186 `low, high, close = ...`）に open を追加し、当バーの決算フラグを算出:
```python
            open_, low, high, close = (float(row["open"]), float(row["low"]),
                                       float(row["high"]), float(row["close"]))
            in_window = eval_start_date is None or df.index[i] >= eval_start_date
            is_gap_bar = i in gaps_in
            in_blackout = earnings_exit_days > 0 and any(i < g <= i + earnings_exit_days
                                                         for g in gaps_all)
```

(e) 保有中決済ブロック（行222-249）を以下に置換（窓fill と決算回避を優先順に挿入）:
```python
            if shares > 0 and entry_i is not None and i > entry_i:
                trailing = trail_atr_mult > 0 and entry_atr is not None
                cur_stop = trailing_stop(stop, high_water, entry_atr, trail_atr_mult, "buy") \
                    if trailing else stop
                if low <= cur_stop:
                    if is_gap_bar and open_ < cur_stop:
                        exit_raw, reason = open_, "gap"     # 決算翌日の窓を寄りで約定
                    else:
                        exit_raw, reason = cur_stop, ("trail" if trailing else "stop")
                elif not trailing and high >= target:
                    exit_raw, reason = target, "target"
                else:
                    exit_raw, reason = None, None
                # 決算跨ぎ回避（終値）: 日中トリガが無くブラックアウト内
                if exit_raw is None and in_blackout:
                    exit_raw, reason = close, "earnings"
                # 時間切れ（終値）
                if exit_raw is None and max_hold_days > 0 and (i - entry_i) >= max_hold_days:
                    exit_raw, reason = close, "time"
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
                    high_water = max(high_water, high)
```

(f) 戻り値（行318 `time_exit_count` の次）に追加:
```python
        "gap_exit_count": sum(1 for c in closed if c["reason"] == "gap"),
        "earnings_exit_count": sum(1 for c in closed if c["reason"] == "earnings"),
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k earnings -q`
Expected: PASS（`test_plan_earnings_default_off_is_unchanged`・`test_plan_earnings_gap_fills_at_open`）

- [ ] **Step 5: 既存バックテストが壊れていないことを確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: バックテストに決算ギャップ再現（窓の寄りfill）と earnings_map 配線（打ち手10）"
```

---

## Task 5: バックテストの決算跨ぎ回避（N営業日前手仕舞い＋約定抑止）

**Files:**
- Modify: `backend/backtest.py`（`_run_backtest_plan` のエントリーブロック・行192付近）
- Test: `backend/test_backtest.py`

Task 4 で決済側（earnings 終値決済・`in_blackout`）と count は実装済み。本タスクはエントリー抑止と回避挙動をテストで固定する。

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_backtest.py` の Task 4 のテスト群の後に追加:

```python
def test_plan_earnings_avoidance_exits_before_gap(monkeypatch):
    """exit_days=1 は窓バーの前日に終値で手仕舞い（理由 earnings）し、窓に到達しない（gap=0）。"""
    bt_mod, _ = _earnings_eval_plan(monkeypatch)
    df = _earnings_df()
    E = df.index[4]   # 窓バー i=5・ブラックアウト(N=1)=i=4
    kw = dict(configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=len(df),
              warmup_days=1, initial_capital=1_000_000,
              cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    r = bt_mod.run_backtest({"X.T": df}, earnings_map={"X.T": [E]}, earnings_exit_days=1, **kw)
    assert r["earnings_exit_count"] == 1
    assert r["gap_exit_count"] == 0                       # 窓に到達しない
    sells = [t for t in r["trades"] if t["action"] == "sell"]
    assert len(sells) == 1 and round(sells[0]["price"], 6) == 110.0   # i=4 終値で手仕舞い


def test_plan_earnings_blackout_blocks_entry(monkeypatch):
    """ブラックアウト中（決算直前）は提示指値に届いても新規約定しない。"""
    import numpy as np, pandas as pd
    bt_mod, _ = _earnings_eval_plan(monkeypatch)
    # i=2 でのみ limit104 に届くが、E=index[2] で i=2 がブラックアウト(N=1, 窓=i=3)になる
    opens  = [100, 100, 104, 130, 130, 130]
    closes = [100, 100, 104, 130, 130, 130]
    highs  = [100, 102, 106, 135, 135, 135]
    lows   = [100,  99, 104, 120, 120, 120]
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-01"), periods=len(closes))
    df = pd.DataFrame({"open": opens, "high": highs, "low": lows,
                       "close": closes, "volume": np.full(len(closes), 1e6)}, index=idx)
    kw = dict(configs=DEFAULT_CONFIGS, exit_mode="plan", backtest_days=len(df),
              warmup_days=1, initial_capital=1_000_000,
              cost={"commission_bps": 0.0, "slippage_bps": 0.0})
    r = bt_mod.run_backtest({"X.T": df}, earnings_map={"X.T": [df.index[2]]},
                            earnings_exit_days=1, **kw)
    assert [t for t in r["trades"] if t["action"] == "buy"] == []   # 約定なし
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k "avoidance or blackout" -q`
Expected: `test_plan_earnings_avoidance_exits_before_gap` は **PASS**（Task 4 の決済側で既に成立）、`test_plan_earnings_blackout_blocks_entry` は **FAIL**（約定抑止が未実装＝買いが入ってしまう）

- [ ] **Step 3: 実装（エントリー抑止）**

`backend/backtest.py` のエントリーブロック（行192-193付近）の約定条件に `not in_blackout` を追加:
```python
            if in_window and shares == 0 and pending is not None and cash > 0:
                if low <= pending["limit"] and not in_blackout:
```
（`in_blackout` は Task 4(d) で当バー算出済み。ブラックアウト中は約定をスキップし、pending は従来どおり有効期限で失効する。）

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -k "avoidance or blackout" -q`
Expected: PASS（2 件）

- [ ] **Step 5: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: バックテストの決算跨ぎ回避（N日前手仕舞い＋ブラックアウト約定抑止・打ち手10）"
```

---

## Task 6: `evaluate_holdout` へ earnings パラメータを貫通

**Files:**
- Modify: `backend/evaluation.py`（`evaluate_holdout` 行80・`_bt` 行101）
- Test: `backend/test_evaluation.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_evaluation.py` の `test_evaluate_holdout_accepts_exit_params`（行140付近）の後に追加:

```python
def test_evaluate_holdout_accepts_earnings_params():
    """evaluate_holdout が earnings_map/earnings_exit_days を受領して完走する（挙動差は backtest 層で担保）。"""
    import pandas as pd
    from market import synthetic_history
    from evaluation import evaluate_holdout
    hist = {f"T{i}.T": synthetic_history(f"T{i}.T", n=160, seed=i) for i in range(2)}
    emap = {t: [df.index[len(df) // 2]] for t, df in hist.items()}
    res = evaluate_holdout(hist, None, earnings_map=emap, earnings_exit_days=1)
    assert "out_of_sample" in res and "chosen_params" in res
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py::test_evaluate_holdout_accepts_earnings_params -q`
Expected: FAIL（`evaluate_holdout() got an unexpected keyword argument 'earnings_map'`）

- [ ] **Step 3: 実装**

`backend/evaluation.py`:

(a) `evaluate_holdout` シグネチャ（行80-83）に追加:
```python
def evaluate_holdout(histories, configs, *, split_ratio=0.7, grid=None, cost=None,
                     initial_capital=3000.0, warmup_days=35, regime_series=None,
                     index_history=None, rs_params=None, risk_pct=None,
                     trail_atr_mult=0.0, max_hold_days=0,
                     earnings_map=None, earnings_exit_days=0) -> dict:
```

(b) 内部 `_bt`（行101-108）の `run_backtest(...)` 呼び出し末尾に貫通:
```python
                            risk_pct=risk_pct,
                            trail_atr_mult=trail_atr_mult, max_hold_days=max_hold_days,
                            earnings_map=earnings_map, earnings_exit_days=earnings_exit_days)
```
（`benchmark` は score モードのため貫通しない＝打ち手9 と同じ。`_run_backtest_plan` 側の `searchsorted` は train/test でスライスされた df に対して窓位置を計算するためスライスに自然対応。）

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -q`
Expected: PASS（全件）

- [ ] **Step 5: コミット**

```bash
git add backend/evaluation.py backend/test_evaluation.py
git commit -m "feat: evaluate_holdout に earnings パラメータを貫通（既定OFF・打ち手10）"
```

---

## Task 7: API 配線（`/backtest`・`/optimize` の earnings ペイロード）

**Files:**
- Modify: `backend/main.py`（`BacktestIn` 行109・`/backtest` 行522・`OptimizeIn` 行573・`/optimize` 行583・import 行24）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_backtest_accepts_exit_params`（行401）の後に追加:

```python
def test_backtest_accepts_earnings_params(client):
    r = client.post("/backtest", json={"demo": True, "days": 40, "exit_mode": "plan",
                                       "earnings_aware": True, "earnings_exit_days": 1})
    assert r.status_code == 200
    body = r.json()
    assert "gap_exit_count" in body and "earnings_exit_count" in body


def test_optimize_accepts_earnings_params(client):
    r = client.post("/optimize", json={"demo": True, "earnings_aware": True,
                                       "earnings_exit_days": 1})
    assert r.status_code == 200
    assert "out_of_sample" in r.json()
```

（注: demo=True では `earnings_map=None`＝決算処理は空振り。本テストは**受領と完走**を固定する。ネットを叩く実取得テストは置かない＝既存方針。）

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k earnings_params -q`
Expected: FAIL（`gap_exit_count` が無い／422 など）

- [ ] **Step 3: 実装**

`backend/main.py`:

(a) import（行24）に `fetch_earnings_dates` を追加:
```python
from market import fetch_earnings_dates, fetch_earnings_days, fetch_name, get_history
```

(b) `BacktestIn`（行109）に2項目追加:
```python
    earnings_aware: bool = False   # True で決算翌日の窓を再現（earnings_map を取得）
    earnings_exit_days: int = 0     # >0 で決算 N 営業日前に手仕舞い（跨ぎ回避）。0=持ち越し
```

(c) `OptimizeIn`（行573）にも同2項目を追加（同文）。

(d) `/backtest`（行550付近・trail/mhd クランプの隣）に earnings_map 構築を追加し `run_backtest` へ:
```python
    eed = max(0, int(payload.earnings_exit_days or 0))
    earnings_map = ({t: fetch_earnings_dates(t) for t in histories}
                    if (payload.earnings_aware and not payload.demo) else None)
```
`run_backtest(...)` 呼び出し（行552-556）末尾に:
```python
                          trail_atr_mult=trail, max_hold_days=mhd,
                          earnings_map=earnings_map, earnings_exit_days=eed)
```

(e) `/optimize`（行599付近）に同様:
```python
    eed = max(0, int(payload.earnings_exit_days or 0))
    earnings_map = ({t: fetch_earnings_dates(t) for t in histories}
                    if (payload.earnings_aware and not payload.demo) else None)
```
`evaluate_holdout(...)` 呼び出し（行601-604）末尾に:
```python
                           trail_atr_mult=trail, max_hold_days=mhd,
                           earnings_map=earnings_map, earnings_exit_days=eed)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -k earnings_params -q`
Expected: PASS（2 件）

- [ ] **Step 5: backend 全体を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（既存133 + 新規9 ≈ 142 件・件数は目安）

- [ ] **Step 6: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: /backtest・/optimize に earnings_aware/earnings_exit_days を配線（打ち手10）"
```

---

## Task 8: フロント — 作戦カードの決算警告

**Files:**
- Modify: `frontend/src/lib/api.ts`（`PlanRow` 型・行68付近）
- Modify: `frontend/src/lib/rows.ts`（`earningsWarning` 純関数・定数）
- Modify: `frontend/src/app/plan/page.tsx`（ヘッダのバッジ・行217付近）
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/lib/__tests__/rows.test.ts` の import に `earningsWarning` を加え、末尾に追加:

```ts
import { earningsWarning } from "@/lib/rows";

describe("earningsWarning", () => {
  it("しきい値以内は { days } を返す", () => {
    expect(earningsWarning(3)).toEqual({ days: 3 });
    expect(earningsWarning(0)).toEqual({ days: 0 });
    expect(earningsWarning(5)).toEqual({ days: 5 });       // 既定しきい値=5（境界含む）
    expect(earningsWarning(7, 7)).toEqual({ days: 7 });    // しきい値は引数で変更可（境界含む）
  });
  it("null・負・しきい値超は null", () => {
    expect(earningsWarning(null)).toBeNull();
    expect(earningsWarning(-1)).toBeNull();
    expect(earningsWarning(6)).toBeNull();
    expect(earningsWarning(8, 7)).toBeNull();              // しきい値超は引数変更後も null
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `npm --prefix frontend test -- --run rows`
Expected: FAIL（`earningsWarning` が export されていない）

- [ ] **Step 3: 実装**

(a) `frontend/src/lib/rows.ts` の末尾に追加:
```ts
/** 決算警告のしきい値（暦日）。fetch_earnings_days が暦日を返すため暦日基準。
 *  ※ バックテストの earnings_exit_days（取引バー基準）とは別単位。 */
export const EARNINGS_WARN_DAYS = 5;

/** 決算までの日数がしきい値以内なら { days } を返す純関数（それ以外 null）。 */
export function earningsWarning(
  daysToEarnings: number | null,
  threshold = EARNINGS_WARN_DAYS,
): { days: number } | null {
  if (daysToEarnings == null || daysToEarnings < 0 || daysToEarnings > threshold) {
    return null;
  }
  return { days: daysToEarnings };
}
```

(b) `frontend/src/lib/api.ts` の `PlanRow` 型（行68-85付近・`confidence` の隣）に追加:
```ts
  days_to_earnings: number | null;
```

(c) `frontend/src/app/plan/page.tsx`:
   - 先頭の rows import に `earningsWarning` を追加（`import { riskSummary, selectTopN } from "@/lib/rows";` → `earningsWarning` を併記）。
   - 確信度バッジ（行213-217）の直後に琥珀バッジを追加:
```tsx
        {row && (() => {
          const w = earningsWarning(row.days_to_earnings);
          return w ? (
            <span className="rounded bg-amber-500 px-1.5 py-0.5 text-xs font-semibold text-white"
                  title="決算跨ぎ注意：保有なら前日までに手仕舞い検討">
              ⚠ {w.days}日後に決算
            </span>
          ) : null;
        })()}
```
（ヘッダ内＝direction を問わず buy/neutral/sell の全カードに表示。）

- [ ] **Step 4: テストが通ることを確認**

Run: `npm --prefix frontend test -- --run rows`
Expected: PASS

- [ ] **Step 5: 型チェック（任意・あれば）**

Run: `npm --prefix frontend run build`（または既存の型チェックコマンド）
Expected: 型エラー無し（`PlanRow.days_to_earnings` 追加が通る）

- [ ] **Step 6: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/api.ts frontend/src/app/plan/page.tsx frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: 作戦カードに『N日後に決算』警告バッジを追加（打ち手10）"
```

---

## Task 9: フロント — バックテスト画面の決算オプション

**Files:**
- Modify: `frontend/src/lib/backtest.ts`（`BacktestForm`・`BacktestBody`・`buildBacktestBody`）
- Modify: `frontend/src/lib/api.ts`（`BacktestResult` 行122・`api.backtest` body 行199）
- Modify: `frontend/src/app/simulation/page.tsx`（入力・内訳）
- Test: `frontend/src/lib/__tests__/backtest.test.ts`

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/lib/__tests__/backtest.test.ts` の `base`（行3付近）に `earningsAware: false, earningsExitDays: 0` を加え、`describe` 末尾に追加:

```ts
  it("earningsAware=false なら earnings 系をペイロードに含めない", () => {
    const b = buildBacktestBody({ ...base, earningsAware: false, earningsExitDays: 1 });
    expect(b.earnings_aware).toBeUndefined();
    expect(b.earnings_exit_days).toBeUndefined();
  });
  it("earningsAware=true で付与・exit_days は >0 のときのみ付与", () => {
    const b1 = buildBacktestBody({ ...base, earningsAware: true, earningsExitDays: 0 });
    expect(b1.earnings_aware).toBe(true);
    expect(b1.earnings_exit_days).toBeUndefined();
    const b2 = buildBacktestBody({ ...base, earningsAware: true, earningsExitDays: 2 });
    expect(b2.earnings_aware).toBe(true);
    expect(b2.earnings_exit_days).toBe(2);
  });
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `npm --prefix frontend test -- --run backtest`
Expected: FAIL（`earnings_aware` が型に無い／付与されない）

- [ ] **Step 3: 実装**

(a) `frontend/src/lib/backtest.ts`:
   - `BacktestForm` に `earningsAware: boolean;` `earningsExitDays: number;` を追加。
   - `BacktestBody` に `earnings_aware?: boolean;` `earnings_exit_days?: number;` を追加。
   - `buildBacktestBody` 末尾（`return body;` の前）に:
```ts
  if (f.earningsAware) body.earnings_aware = true;
  if (f.earningsAware && f.earningsExitDays > 0) body.earnings_exit_days = f.earningsExitDays;
```

(b) `frontend/src/lib/api.ts`:
   - `BacktestResult`（行122）に `gap_exit_count?: number;` `earnings_exit_count?: number;` を追加。
   - `api.backtest` の body 型（行199）に `earnings_aware?: boolean; earnings_exit_days?: number;` を追加。

(c) `frontend/src/app/simulation/page.tsx`:
   - state 追加（行24-25 の隣）: `const [earningsAware, setEarningsAware] = useState(false);` `const [earningsExitDays, setEarningsExitDays] = useState(0);`
   - `buildBacktestBody({...})` 呼び出し（行35）に `earningsAware, earningsExitDays` を追加。
   - `atrExit` ブロック（行81-）と並ぶ位置に入力 UI を追加:
```tsx
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={earningsAware}
                   onChange={(e) => setEarningsAware(e.target.checked)} />
            決算を考慮（決算翌日の窓を再現）
          </label>
          {earningsAware && (
            <label className="flex items-center gap-2 text-sm">
              決算の
              <input type="number" min={0} className="w-16 rounded border px-1"
                     value={earningsExitDays}
                     onChange={(e) => setEarningsExitDays(Number(e.target.value))} />
              営業日前に手仕舞い（0=持ち越し）
            </label>
          )}
```
   - 内訳 Metric（行150-151 の trail/time の隣）に追加:
```tsx
                <Metric label="決算ギャップで決済" value={`${result.gap_exit_count ?? 0} 回`} />
                <Metric label="決算回避で手仕舞い" value={`${result.earnings_exit_count ?? 0} 回`} />
```

- [ ] **Step 4: テストが通ることを確認**

Run: `npm --prefix frontend test -- --run backtest`
Expected: PASS

- [ ] **Step 5: フロント全体を確認**

Run: `npm --prefix frontend test`
Expected: PASS（既存19 + 新規 ≈ 23 件）

- [ ] **Step 6: コミット**

```bash
git add frontend/src/lib/backtest.ts frontend/src/lib/api.ts frontend/src/app/simulation/page.tsx frontend/src/lib/__tests__/backtest.test.ts
git commit -m "feat: バックテスト画面に決算考慮/跨ぎ回避の入力と内訳表示を追加（打ち手10）"
```

---

## 完了基準

- [ ] backend 全テスト PASS（`backend/venv/bin/python -m pytest backend/ -q`・既存133件不変＋新規）
- [ ] frontend 全テスト PASS（`npm --prefix frontend test`・既存19件不変＋新規）
- [ ] `earnings_aware=False`／`earnings_map=None` で現挙動完全再現（回帰テストで固定済み）
- [ ] `DEFAULT_CONFIGS` 14 件不変（`test_api` 件数アサート維持）
- [ ] 最終 code-review → finishing-a-development-branch で main へ FF マージ＋`git push origin main`

## 実装順の注意

- Task 4 → 5 は同一 `_run_backtest_plan` を触る（4 が決済側＋配線、5 がエントリー抑止）。順守する。
- Task 7（API）は Task 1・4・5・6 に依存（`fetch_earnings_dates`・`run_backtest`/`evaluate_holdout` の earnings 引数）。
- Task 8・9 は独立（フロント）。Task 2-3（days_to_earnings 永続化）後なら Task 8 の `/plan` 応答に列が乗る。
- 各バックテストテストは [[signal-tests-prefer-deterministic-ohlc]] に従い統制 OHLC で決定論化（seed 走査しない）。
