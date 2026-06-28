# 打ち手12 流動性フィルター/データ健全性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 薄商い銘柄の警告＋推奨除外と、作戦の土台データの健全性可視化を、戦略ロジックを一切変えずに加える（実約定の現実性を高める安全機能）。

**Architecture:** 新純関数モジュール `backend/data_quality.py`（DB/ネット非依存・例外を投げない）が `average_turnover` と `data_health` を提供。`daily_plan` に2列（`avg_turnover REAL`/`data_health TEXT`）を冪等追加し、`perform_refresh` が取得済み df から算出して保存。frontend は生値をしきい値判定して薄商いバッジ・データ注意を表示し、`selectTopN` が薄商いを「今夜の推奨」から除外する。score/direction/confidence/sizing/backtest は不変（加法的・後方互換）。

**Tech Stack:** Python 3 / pandas / numpy（`np.busday_count` + `holidays_jp` で祝日対応の取引日距離）/ SQLite、Next.js / TypeScript / vitest。

**Spec:** `docs/superpowers/specs/2026-06-28-liquidity-data-health-design.md`

**テスト実行:** backend `backend/venv/bin/python -m pytest backend/ -q`（現状166）／ frontend `npm --prefix frontend test`（現状25・vitest）／ 型 `npm --prefix frontend run build`。

---

## File Structure

- **Create** `backend/data_quality.py` — 純関数2つ（`average_turnover`/`data_health`）。DB/ネット非依存・例外を投げない。
- **Create** `backend/test_data_quality.py` — 上記の統制データ単体テスト。
- **Modify** `backend/db.py` — `daily_plan` スキーマ＋`_migrate_daily_plan`＋`upsert_plan` に2列。
- **Modify** `backend/main.py` — `perform_refresh` で算出・配線（import 追加）。
- **Modify** `backend/test_api.py` — 移行テスト＋perform_refresh(demo)永続化テスト。
- **Modify** `frontend/src/lib/api.ts` — `PlanRow` に2フィールド。
- **Modify** `frontend/src/lib/rows.ts` — `LIQUIDITY_MIN_YEN`/`liquidityWarning`/`dataHealthWarnings`＋`selectTopN` 除外。
- **Modify** `frontend/src/lib/__tests__/rows.test.ts` — 上記の単体テスト。
- **Modify** `frontend/src/app/plan/page.tsx` — `PlanCard` に薄商いバッジ＋データ注意（display 専用）。

**ウィンドウの取り決め（実装者向け・spec-review 指摘）:** `data_health` は意図的に3メトリクスで窓が微妙に異なる。`recent = df.tail(window+1)`（spike のリターン計算に1本余分）→ ①`zero_volume_days`= `recent` 末尾 `window` 本 ②`spike_days`= `recent` 全体の `pct_change()`（= `window` リターン）③`gap_days`= 末尾 `window` 日付の連続遷移（= `window-1` 遷移）。各々独立に正しい。off-by-one を「発見」せず最初から意図して書くこと。

---

## Task 1: `data_quality.average_turnover`（平均売買代金）

**Files:**
- Create: `backend/data_quality.py`
- Test: `backend/test_data_quality.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_data_quality.py`:
```python
"""data_quality.py のネット/DB非依存テスト（統制 OHLC で決定論化）。打ち手12。"""
import numpy as np
import pandas as pd

import data_quality as dq
import holidays_jp


def _trading_days(n: int, end: str = "2026-06-26") -> pd.DatetimeIndex:
    """末尾 n 本の東証営業日（祝日を除く）。連続営業日の busday_count が 1 になるように。"""
    days = [d for d in pd.bdate_range(end=end, periods=n + 40)
            if d.strftime("%Y-%m-%d") not in holidays_jp.MARKET_HOLIDAYS]
    return pd.DatetimeIndex(days[-n:])


def _df(closes, volumes, dates=None) -> pd.DataFrame:
    closes = [float(c) for c in closes]
    if dates is None:
        dates = _trading_days(len(closes))
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes},
        index=pd.DatetimeIndex(dates),
    )


def test_average_turnover_basic():
    df = _df([100.0] * 20, [10_000] * 20)
    assert dq.average_turnover(df, window=20) == 1_000_000.0


def test_average_turnover_insufficient_returns_none():
    df = _df([100.0] * 10, [10_000] * 10)
    assert dq.average_turnover(df, window=20) is None


def test_average_turnover_missing_column_returns_none():
    df = _df([100.0] * 20, [10_000] * 20).drop(columns=["volume"])
    assert dq.average_turnover(df, window=20) is None


def test_average_turnover_all_zero_volume_is_zero():
    df = _df([100.0] * 20, [0] * 20)
    assert dq.average_turnover(df, window=20) == 0.0


def test_average_turnover_garbage_returns_none():
    assert dq.average_turnover(None) is None
    assert dq.average_turnover(pd.DataFrame()) is None
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_data_quality.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'data_quality'`）

- [ ] **Step 3: 最小実装（`average_turnover` のみ）**

`backend/data_quality.py`:
```python
"""データ健全性・流動性の純関数（DB/ネット非依存）。打ち手12。

average_turnover: 直近 window バーの平均売買代金（close×volume・円）。
data_health: 直近 window バーの {zero_volume_days, gap_days, spike_days}。
いずれも不正入力・空・列欠落・nan を安全な既定で返し、例外を投げない
（market の best-effort 契約・signals.volume_ratio の None 返しと同方針）。
"""
from __future__ import annotations

import numpy as np

from holidays_jp import MARKET_HOLIDAYS

# 連続欠損のしきい値: 祝日対応の取引日距離 >= 2（＝1取引日以上の欠損）。
_GAP_BDAYS = 2


def average_turnover(df, window: int = 20) -> float | None:
    """平均売買代金（円・直近 window バーの close×volume の平均）。

    列欠落 / len(df) < window / nan は None（算出不可＝不明）。例外を投げない。
    """
    try:
        if df is None or "close" not in df.columns or "volume" not in df.columns:
            return None
        if len(df) < window:
            return None
        turnover = (df["close"].astype(float) * df["volume"].astype(float)).tail(window).mean()
        if turnover != turnover:        # nan
            return None
        return float(turnover)
    except Exception:
        return None
```

- [ ] **Step 4: テスト成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_data_quality.py -q`
Expected: PASS（5件）

- [ ] **Step 5: コミット**

```bash
git add backend/data_quality.py backend/test_data_quality.py
git commit -m "feat: data_quality.average_turnover（平均売買代金・打ち手12）"
```

---

## Task 2: `data_quality.data_health`（出来高0/欠損/スパイク）

**Files:**
- Modify: `backend/data_quality.py`
- Test: `backend/test_data_quality.py`

- [ ] **Step 1: 失敗するテストを追加**

`backend/test_data_quality.py` に追記:
```python
_ZERO = {"zero_volume_days": 0, "gap_days": 0, "spike_days": 0}


def test_data_health_clean():
    df = _df([100.0 + i for i in range(25)], [1_000_000] * 25)
    assert dq.data_health(df) == _ZERO


def test_data_health_zero_volume_days():
    vols = [1_000_000] * 25
    vols[-1] = 0
    vols[-3] = 0
    df = _df([100.0 + i * 0.1 for i in range(25)], vols)
    assert dq.data_health(df)["zero_volume_days"] == 2


def test_data_health_spike_days():
    closes = [100.0] * 25
    closes[-2] = 300.0   # +200% → 翌日 -66.7%。どちらも |変化|>50% ＝ 2本
    df = _df(closes, [1_000_000] * 25)
    assert dq.data_health(df)["spike_days"] == 2


def test_data_health_no_spike_below_threshold():
    closes = [100.0] * 25
    closes[-2] = 120.0   # +20% / -16.7% ＝ どちらも <50%
    df = _df(closes, [1_000_000] * 25)
    assert dq.data_health(df)["spike_days"] == 0


def test_data_health_gap_detected():
    dates = list(_trading_days(25))
    del dates[-3]        # 取引日を1本抜く＝その前後で取引日距離 2 のギャップ
    df = _df([100.0 + i * 0.1 for i in range(24)], [1_000_000] * 24, dates=dates)
    assert dq.data_health(df, window=24)["gap_days"] >= 1


def test_data_health_holiday_not_flagged_as_gap():
    """2026 GW（4/29・5/3-5/6 休場）を跨ぐ連続営業日は欠損ではない。"""
    biz = _trading_days(20, end="2026-05-15")   # GW を含む直近営業日
    df = _df([100.0 + i * 0.1 for i in range(20)], [1_000_000] * 20, dates=biz)
    assert dq.data_health(df, window=20)["gap_days"] == 0


def test_data_health_garbage_returns_zero():
    assert dq.data_health(None) == _ZERO
    assert dq.data_health(pd.DataFrame()) == _ZERO
    assert dq.data_health(_df([100.0] * 5, [1_000_000] * 5).drop(columns=["close"])) == _ZERO
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_data_quality.py -k data_health -q`
Expected: FAIL（`AttributeError: module 'data_quality' has no attribute 'data_health'`）

- [ ] **Step 3: `data_health` を実装（`data_quality.py` に追記）**

```python
def data_health(df, window: int = 20, spike_pct: float = 0.5) -> dict:
    """直近 window バーのデータ健全性カウント。

    戻り: {"zero_volume_days", "gap_days", "spike_days"}（すべて 0 以上の int）。
    不正入力・空・列欠落は全 0。例外を投げない。
    """
    zero = {"zero_volume_days": 0, "gap_days": 0, "spike_days": 0}
    try:
        if df is None or len(df) == 0 or "close" not in df.columns:
            return dict(zero)
        recent = df.tail(window + 1)        # +1: spike のリターン計算に1本余分
        out = dict(zero)

        # 出来高0日（直近 window 本）
        if "volume" in recent.columns:
            vol = recent["volume"].tail(window).astype(float)
            out["zero_volume_days"] = int(((vol <= 0) | vol.isna()).sum())

        # 異常スパイク（|日次変化率| > spike_pct）
        rets = recent["close"].astype(float).pct_change().dropna()
        out["spike_days"] = int((rets.abs() > spike_pct).sum())

        # 連続欠損（祝日対応の取引日距離 >= _GAP_BDAYS）
        dates = recent.index[-window:]
        if len(dates) >= 2:
            cal = np.busdaycalendar(holidays=sorted(MARKET_HOLIDAYS))
            d = dates.normalize().values.astype("datetime64[D]")   # np.busday_count は datetime64[D] 必須
            gaps = np.busday_count(d[:-1], d[1:], busdaycal=cal)
            out["gap_days"] = int((gaps >= _GAP_BDAYS).sum())

        return out
    except Exception:
        return dict(zero)
```

- [ ] **Step 4: テスト成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_data_quality.py -q`
Expected: PASS（12件）
（万一 `test_data_health_holiday_not_flagged_as_gap` が落ちたら、`_trading_days` の end をずらして GW を確実に含む20本にする。busday_count の挙動は spec-review で numpy 2.2.6 で検証済み＝連続営業日は 1・1欠損は 2。）

- [ ] **Step 5: コミット**

```bash
git add backend/data_quality.py backend/test_data_quality.py
git commit -m "feat: data_quality.data_health（出来高0/欠損/スパイク・打ち手12）"
```

---

## Task 3: `daily_plan` に2列 + `upsert_plan` 配線

**Files:**
- Modify: `backend/db.py`（SCHEMA / `_migrate_daily_plan` / `upsert_plan`）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗する移行テストを追加**

`backend/test_api.py` に追記（`test_daily_plan_earnings_column_migration` の隣に置くと文脈が揃う）:
```python
def test_daily_plan_liquidity_column_migration(tmp_path, monkeypatch):
    """既存 daily_plan に avg_turnover/data_health が無くても冪等マイグレーションで追加され、upsert で値が入る。"""
    import sqlite3
    import db as dbmod
    dbfile = tmp_path / "old_liq.db"
    conn = sqlite3.connect(dbfile)
    # 打ち手11 相当（結果列まで・avg_turnover/data_health 無し）の旧スキーマ
    conn.execute(
        "CREATE TABLE daily_plan (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT NOT NULL, plan_date TEXT NOT NULL, direction TEXT, score INTEGER, "
        "vol_ratio REAL, weekly_trend TEXT, limit_price REAL, stop_price REAL, "
        "target_price REAL, rationale TEXT, confidence REAL, shares REAL, risk_amount REAL, "
        "days_to_earnings INTEGER, ai_summary TEXT, ai_confidence INTEGER, ai_risks TEXT, "
        "regime TEXT, fill_status TEXT, outcome TEXT, exit_price REAL, result_r REAL, "
        "days_held INTEGER, resolved_date TEXT, created_at TEXT, UNIQUE(ticker, plan_date))")
    conn.commit(); conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()
    with dbmod.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_plan)")}
    assert "avg_turnover" in cols and "data_health" in cols
    dbmod.upsert_plan({"ticker": "L.T", "plan_date": "2026-06-01", "direction": "buy",
                       "score": 3, "vol_ratio": None, "weekly_trend": None,
                       "limit_price": 1000.0, "stop_price": 950.0, "target_price": 1100.0,
                       "rationale": "x", "avg_turnover": 50_000_000.0,
                       "data_health": '{"zero_volume_days":1,"gap_days":0,"spike_days":0}'})
    row = [r for r in dbmod.list_plan("2026-06-01") if r["ticker"] == "L.T"][0]
    assert row["avg_turnover"] == 50_000_000.0
    assert row["data_health"] == '{"zero_volume_days":1,"gap_days":0,"spike_days":0}'
    dbmod.init_db()   # 冪等（再実行で例外なし）
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_daily_plan_liquidity_column_migration -q`
Expected: FAIL（`avg_turnover not in cols` で AssertionError、または upsert で `no column named avg_turnover`）

- [ ] **Step 3a: SCHEMA に2列追加**（`backend/db.py` の `daily_plan` 定義）

`resolved_date TEXT,` の直後（`created_at` の前）に追加:
```sql
  resolved_date TEXT,
  avg_turnover  REAL,
  data_health   TEXT,
  created_at    TEXT DEFAULT (datetime('now')),
```

- [ ] **Step 3b: `_migrate_daily_plan` の列タプルに追加**

`("result_r", "REAL"), ("days_held", "INTEGER"), ("resolved_date", "TEXT")` に続けて:
```python
                      ("result_r", "REAL"), ("days_held", "INTEGER"), ("resolved_date", "TEXT"),
                      ("avg_turnover", "REAL"), ("data_health", "TEXT")):
```

- [ ] **Step 3c: `upsert_plan` の3箇所に配線**

(1) `setdefault` ループのキー:
```python
    for k in ("ai_summary", "ai_confidence", "ai_risks", "confidence", "shares",
              "risk_amount", "days_to_earnings", "regime", "avg_turnover", "data_health"):
        row.setdefault(k, None)
```
(2) INSERT 列リストと VALUES（`ai_risks` の直後に追加）:
```sql
    "INSERT INTO daily_plan "
    "(ticker, plan_date, direction, score, vol_ratio, weekly_trend, "
    " limit_price, stop_price, target_price, rationale, confidence, "
    " shares, risk_amount, days_to_earnings, regime, ai_summary, ai_confidence, ai_risks, "
    " avg_turnover, data_health) "
    "VALUES (:ticker, :plan_date, :direction, :score, :vol_ratio, :weekly_trend, "
    " :limit_price, :stop_price, :target_price, :rationale, :confidence, "
    " :shares, :risk_amount, :days_to_earnings, :regime, :ai_summary, :ai_confidence, :ai_risks, "
    " :avg_turnover, :data_health) "
```
(3) `ON CONFLICT … DO UPDATE SET`（`ai_risks=excluded.ai_risks,` の直後・`created_at=` の前。生算出値ゆえ COALESCE せず素の `excluded.*` で上書き＝健全→不健全/不健全→健全 の遷移を正しく反映）:
```sql
    "ai_summary=excluded.ai_summary, "
    "ai_confidence=excluded.ai_confidence, ai_risks=excluded.ai_risks, "
    "avg_turnover=excluded.avg_turnover, data_health=excluded.data_health, "
    "created_at=datetime('now')",
```

- [ ] **Step 4: テスト成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_daily_plan_liquidity_column_migration -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/db.py backend/test_api.py
git commit -m "feat: daily_plan に avg_turnover/data_health を冪等追加＋upsert配線（打ち手12）"
```

---

## Task 4: `perform_refresh` で算出・保存

**Files:**
- Modify: `backend/main.py`
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗する永続化テストを追加**

`backend/test_api.py` に追記（`test_perform_refresh_persists_days_to_earnings` の隣）:
```python
def test_perform_refresh_persists_liquidity(client):
    """perform_refresh(demo) が avg_turnover を daily_plan に永続化する（純算出ゆえ demo でも出る）。"""
    client.post("/plan/generate?demo=true")
    rows = client.get("/plan").json()["rows"]
    assert rows
    # 合成データは出来高1M〜8M・価格〜2500 ＝ 売買代金は十分（数値・正）
    assert all(r["avg_turnover"] is not None and r["avg_turnover"] > 0 for r in rows)
    # data_health キーが必ず存在する（健全なら None）
    assert all("data_health" in r for r in rows)
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_perform_refresh_persists_liquidity -q`
Expected: FAIL（`KeyError: 'avg_turnover'` または `avg_turnover is None`。※ `list_plan` は `SELECT *` ゆえ列追加後はキーが出る。列未配線なら upsert で値が入らず None）

- [ ] **Step 3a: import 追加**（`backend/main.py` 冒頭の import 群）

`from costs import cost_from_configs` の近くに:
```python
from data_quality import average_turnover, data_health
```

- [ ] **Step 3b: `perform_refresh` のループ内で算出**

`db.upsert_prices(ticker, df)` の直後あたり（df が空でないと確定済みの箇所）に:
```python
        turnover = average_turnover(df)
        health = data_health(df)
```

- [ ] **Step 3c: `db.upsert_plan({...})` の dict に2キー追加**

`"days_to_earnings": days_to_earnings, "regime": regime,` の行に続けて:
```python
            "avg_turnover": turnover,
            "data_health": json.dumps(health) if any(health.values()) else None,
```
（`json` は既に import 済み。健全＝全0 のとき None で DB を埋めない。）

- [ ] **Step 4: テスト成功を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_perform_refresh_persists_liquidity -q`
Expected: PASS

- [ ] **Step 5: backend 全体の回帰**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全 PASS（166 + 新規6[data_quality] + 2[api] ≈ 174 件）。落ちたら修正してから次へ。

- [ ] **Step 6: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: perform_refresh で avg_turnover/data_health を算出・保存（打ち手12）"
```

---

## Task 5: frontend `liquidityWarning` + `LIQUIDITY_MIN_YEN`

**Files:**
- Modify: `frontend/src/lib/rows.ts`
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを追加**

`frontend/src/lib/__tests__/rows.test.ts` の import に `liquidityWarning, dataHealthWarnings, LIQUIDITY_MIN_YEN` を足し、末尾に追記:
```typescript
describe("liquidityWarning", () => {
  it("平均売買代金が閾値未満なら {turnover}", () => {
    expect(liquidityWarning(50_000_000)).toEqual({ turnover: 50_000_000 });
  });
  it("閾値以上は null", () => {
    expect(liquidityWarning(LIQUIDITY_MIN_YEN)).toBeNull();
    expect(liquidityWarning(500_000_000)).toBeNull();
  });
  it("null（不明・旧行）は警告しない", () => {
    expect(liquidityWarning(null)).toBeNull();
  });
});
```

- [ ] **Step 2: 失敗を確認**

Run: `npm --prefix frontend test`
Expected: FAIL（`liquidityWarning is not exported` 等）

- [ ] **Step 3: 実装（`frontend/src/lib/rows.ts` 末尾に追記）**

```typescript
/** 薄商い警告のしきい値（円・平均売買代金/日）。1億円未満を「実約定に難あり」とみなす。
 *  ※ 個人の実約定可能性の目安。設定化は将来。決算の EARNINGS_WARN_DAYS と同型のフロント定数。 */
export const LIQUIDITY_MIN_YEN = 100_000_000;

/** 平均売買代金がしきい値未満なら {turnover} を返す純関数（それ以外 null）。
 *  null（不明・旧行）は警告しない＝「不明=非干渉」。 */
export function liquidityWarning(
  avgTurnover: number | null,
  threshold = LIQUIDITY_MIN_YEN,
): { turnover: number } | null {
  if (avgTurnover == null || avgTurnover >= threshold) return null;
  return { turnover: avgTurnover };
}
```

- [ ] **Step 4: テスト成功を確認**

Run: `npm --prefix frontend test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: liquidityWarning + LIQUIDITY_MIN_YEN（打ち手12・フロント）"
```

---

## Task 6: frontend `dataHealthWarnings`

**Files:**
- Modify: `frontend/src/lib/rows.ts`
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを追加**

```typescript
describe("dataHealthWarnings", () => {
  it("各カウント>0 を文言化（順序: 出来高0→欠損→スパイク）", () => {
    const j = JSON.stringify({ zero_volume_days: 2, gap_days: 1, spike_days: 3 });
    expect(dataHealthWarnings(j)).toEqual([
      "出来高0の日が2日",
      "データ欠損 1件",
      "異常な値動き 3件（データ要確認）",
    ]);
  });
  it("全0は空配列", () => {
    expect(dataHealthWarnings(JSON.stringify({ zero_volume_days: 0, gap_days: 0, spike_days: 0 }))).toEqual([]);
  });
  it("null・壊れJSONは空配列", () => {
    expect(dataHealthWarnings(null)).toEqual([]);
    expect(dataHealthWarnings("{not json")).toEqual([]);
  });
});
```

- [ ] **Step 2: 失敗を確認**

Run: `npm --prefix frontend test`
Expected: FAIL（`dataHealthWarnings is not exported`）

- [ ] **Step 3: 実装（`frontend/src/lib/rows.ts` 末尾に追記）**

```typescript
/** data_health（JSON文字列）を人間可読な注意文の配列に。null/健全/壊れJSON は []。
 *  ai_risks と同様、生データをフロントで表示用に整形する（しきい値はカウント>0）。 */
export function dataHealthWarnings(json: string | null): string[] {
  if (!json) return [];
  let h: { zero_volume_days?: number; gap_days?: number; spike_days?: number };
  try {
    h = JSON.parse(json);
  } catch {
    return [];
  }
  if (!h || typeof h !== "object") return [];
  const out: string[] = [];
  if ((h.zero_volume_days ?? 0) > 0) out.push(`出来高0の日が${h.zero_volume_days}日`);
  if ((h.gap_days ?? 0) > 0) out.push(`データ欠損 ${h.gap_days}件`);
  if ((h.spike_days ?? 0) > 0) out.push(`異常な値動き ${h.spike_days}件（データ要確認）`);
  return out;
}
```

- [ ] **Step 4: テスト成功を確認**

Run: `npm --prefix frontend test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: dataHealthWarnings（打ち手12・フロント）"
```

---

## Task 7: `selectTopN` 薄商い除外 + `PlanRow` 型

**Files:**
- Modify: `frontend/src/lib/api.ts`（`PlanRow`）
- Modify: `frontend/src/lib/rows.ts`（`Rankable`/`selectTopN`）
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを追加**

```typescript
describe("selectTopN 薄商い除外", () => {
  const mk = (ticker: string, confidence: number, avg_turnover: number | null) =>
    ({ ticker, direction: "buy" as const, score: 3, confidence, avg_turnover });
  it("薄商い（閾値未満）を推奨から除外し、流動的は残す", () => {
    const rows = [mk("A", 80, 5_000_000), mk("B", 70, 500_000_000)];
    expect(selectTopN(rows, 3).map((r) => r.ticker)).toEqual(["B"]);
  });
  it("avg_turnover が null は除外しない（後方互換・confidence とは非対称）", () => {
    const rows = [mk("A", 80, null)];
    expect(selectTopN(rows, 3).map((r) => r.ticker)).toEqual(["A"]);
  });
});
```

- [ ] **Step 2: 失敗を確認**

Run: `npm --prefix frontend test`
Expected: FAIL（薄商い A が除外されず `["B","A"]` になる、もしくは型エラー）

- [ ] **Step 3a: `Rankable` に任意フィールド追加 + `selectTopN` 拡張**（`frontend/src/lib/rows.ts`）

`Rankable` 型に `avg_turnover?` を任意追加（既存テストのリテラルを壊さない）:
```typescript
type Rankable = { ticker: string; direction: Direction; score: number; confidence: number | null; avg_turnover?: number | null };
```
`selectTopN` に薄商い除外フィルタを追加（**null は通す**＝既知かつ閾値未満のみ除外）:
```typescript
export function selectTopN<T extends Rankable>(rows: T[], n: number): T[] {
  return n <= 0
    ? []
    : rankByConfidence(rows)
        .filter((r) => (r.confidence ?? 0) > 0)
        .filter((r) => r.avg_turnover == null || r.avg_turnover >= LIQUIDITY_MIN_YEN)
        .slice(0, n);
}
```
（`LIQUIDITY_MIN_YEN` は Task 5 で定義済み。`rankByConfidence` は無改変。）

- [ ] **Step 3b: `PlanRow` に2フィールド追加**（`frontend/src/lib/api.ts`）

`days_to_earnings: number | null;` の近くに追加:
```typescript
  avg_turnover: number | null;   // 平均売買代金（円・直近20日）。打ち手12
  data_health: string | null;    // JSON: {zero_volume_days,gap_days,spike_days}。健全/旧行は null
```

- [ ] **Step 4: テスト成功 + 型チェック**

Run: `npm --prefix frontend test`
Expected: PASS
Run: `npm --prefix frontend run build`
Expected: 型エラーなしでビルド成功（`PlanRow` は `selectTopN` の引数として `avg_turnover` を満たす）

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/api.ts frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: selectTopN が薄商いを推奨除外＋PlanRow型（打ち手12・フロント）"
```

---

## Task 8: `PlanCard` に薄商いバッジ＋データ注意（表示）

**Files:**
- Modify: `frontend/src/app/plan/page.tsx`

display 専用（純関数の単体テストは Task 5-7 で担保済み）。検証は型チェック＋ビルド＋目視。

- [ ] **Step 1: import に2関数を追加**

`import { earningsWarning, riskSummary, selectTopN } from "@/lib/rows";` を:
```typescript
import { dataHealthWarnings, earningsWarning, liquidityWarning, riskSummary, selectTopN } from "@/lib/rows";
```

- [ ] **Step 2: 薄商いバッジを追加**（`PlanCard` の決算バッジ `⚠ {w.days}日後に決算` のブロック直後）

```tsx
        {row && (() => {
          const lw = liquidityWarning(row.avg_turnover);
          return lw ? (
            <span className="rounded bg-amber-600 px-1.5 py-0.5 text-xs font-semibold text-white"
                  title="薄商い：提案指値は約定しづらく滑りやすい（推奨からは除外）">
              ⚠ 薄商い 売買代金 約{Math.round(lw.turnover / 1e6).toLocaleString()}百万円/日
            </span>
          ) : null;
        })()}
```

- [ ] **Step 3: データ注意の注記を追加**（ヘッダの `flex` div を閉じた直後、`<HoldingEditor … />` の前）

```tsx
      {row && (() => {
        const dh = dataHealthWarnings(row.data_health);
        return dh.length ? (
          <p className="mb-2 text-xs text-amber-700">データ注意: {dh.join(" / ")}</p>
        ) : null;
      })()}
```

- [ ] **Step 4: 「今夜の推奨」注記に一言追加**（薄商い除外の説明・任意の UX 補助）

`<p className="mb-2 text-xs text-slate-500">確信度の高い順。下の全リストにも再掲されます。</p>` を:
```tsx
          <p className="mb-2 text-xs text-slate-500">確信度の高い順。薄商い銘柄は除外しています。下の全リストにも再掲されます。</p>
```

- [ ] **Step 5: 型チェック＋ビルド＋テスト**

Run: `npm --prefix frontend run build`
Expected: 型エラーなし・ビルド成功
Run: `npm --prefix frontend test`
Expected: 全 PASS（25 + 新規）

- [ ] **Step 6: コミット**

```bash
git add frontend/src/app/plan/page.tsx
git commit -m "feat: 作戦カードに薄商いバッジ＋データ注意（打ち手12・フロント）"
```

---

## Task 9: 最終回帰 + code-review

- [ ] **Step 1: backend 全テスト**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: 全 PASS

- [ ] **Step 2: frontend テスト＋ビルド**

Run: `npm --prefix frontend test && npm --prefix frontend run build`
Expected: 全 PASS・ビルド成功

- [ ] **Step 3: 加法的・後方互換の確認（score/direction/confidence/backtest 不変）**

`git diff main --stat` で変更範囲が「新規2ファイル＋db/main/api/rows/plan.tsx＋テスト」に限られ、`signals.py`/`backtest.py`/`evaluation.py` に変更が無いことを確認（戦略ロジック不変の証跡）。

- [ ] **Step 4: code-review（superpowers:requesting-code-review）**

実装完了後、`superpowers:requesting-code-review` で最終レビュー。致命指摘があれば `superpowers:receiving-code-review` で技術的に吟味して対応。

- [ ] **Step 5: finishing-a-development-branch**

`superpowers:finishing-a-development-branch` で main へ fast-forward マージ＋`git push origin main`。

---

## 落とし穴・非自明点（再掲）

1. **`selectTopN` の null 通過は意図的**（confidence とは非対称）。`avg_turnover==null`（旧行・<20バー新規銘柄・算出不可）は**除外しない**。誤って null も除外すると移行直後に推奨が全消えする。confidence は `?? 0`（不明=非推奨）だが流動性は `== null` で通す（不明=現実性は判定保留=推奨を消さない）。
2. **`data_health` の JSON 保存は issue があるときだけ**（全0は None）。`dataHealthWarnings(null)→[]` が対応。
3. **`np.busday_count` は `datetime64[D]` 必須**＝`dates.normalize().values.astype("datetime64[D]")` で変換。連続営業日=1・1取引日欠損=2（spec-review が numpy 2.2.6 で検証済み）。
4. **demo でも算出**（earnings と違い純算出）。
5. **backtest 非干渉**＝`signals.py`/`backtest.py`/`evaluation.py`/`paper_trades` は無改変。流動性はライブ作戦ボードの表示・推奨選別のみに作用（戦略の期待値・DD を動かさない＝診断不要の根拠）。
6. **テストは統制 OHLC で決定論化**（[[signal-tests-prefer-deterministic-ohlc]]）。合成 seed 走査はしない。
</content>
