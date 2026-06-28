# 打ち手11 実績トラッキング Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成済み `daily_plan` の実結果を、その後の実 OHLC（`price_data`）から自動追跡・記録し、型別（レジーム×方向）に集計して可視化する（課題6 学習ループの測定基盤）。

**Architecture:** 判定は純関数 `backend/tracking.py`（DB/ネット非依存）に分離。`daily_plan` に結果列を冪等追加し、`perform_refresh` が `regime` を保存しつつループ後に `resolve_plan_outcomes()`（`price_data` から stateless 再計算・冪等）で過去作戦を解決。`GET /performance` ＋最小フロントで型別成績を表示。スコア/出口/バックテストは不変。

**Tech Stack:** Python / FastAPI / pandas / pytest（backend, 現状152件）／ Next.js + vitest（frontend, 23件）。

**Spec:** `docs/superpowers/specs/2026-06-28-plan-outcome-tracking-design.md`

**前提メモリ:** [[roadmap-progress]]

**テスト実行:** backend `backend/venv/bin/python -m pytest backend/ -q`（cwd 注意・絶対パス・全スイートは ~2分でバックグラウンド推奨）／ frontend `npm --prefix frontend test`

## File Structure
- Create `backend/tracking.py` — 純関数 `resolve_outcome` / `plan_type` / `aggregate_performance`。1責務＝結果判定と集計。
- Create `backend/test_tracking.py` — 上記の単体テスト。
- Modify `backend/db.py` — `_migrate_daily_plan` 列追加・`upsert_plan` に regime 配線・`resolve_plan_outcomes` / `performance_summary` 追加。
- Modify `backend/main.py` — `perform_refresh` で regime 保存＆resolve 呼び出し・`GET /performance`。
- Modify `backend/test_api.py` — 移行・解決結合・endpoint。
- Modify frontend（`api.ts` ＋新ページ `app/performance/page.tsx` ＋ナビ）＋ test。

---

## Task 1: tracking.py 純関数（判定・型・集計）

**Files:** Create `backend/tracking.py`, `backend/test_tracking.py`

- [ ] **Step 1: 失敗するテストを書く** — `backend/test_tracking.py`:

```python
import tracking as tk


def _bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


BUY = {"direction": "buy", "limit_price": 100.0, "stop_price": 90.0, "target_price": 140.0}
# entry=limit=100, risk=entry-stop=10, target R=(140-100)/10=4.0


def test_buy_fill_then_target():
    bars = [_bar("d0", 101, 102, 99, 101),    # bar0: low99<=100 → 約定
            _bar("d1", 120, 141, 119, 140)]    # bar1: high141>=140 → target
    r = tk.resolve_outcome(BUY, bars)
    assert r["fill_status"] == "filled" and r["outcome"] == "target"
    assert abs(r["result_r"] - 4.0) < 1e-9 and r["exit_price"] == 140.0
    assert r["days_held"] == 1 and r["resolved_date"] == "d1"


def test_buy_fill_then_stop_is_minus_1r():
    bars = [_bar("d0", 101, 102, 99, 101), _bar("d1", 98, 99, 89, 90)]  # bar1 low89<=90 stop
    r = tk.resolve_outcome(BUY, bars)
    assert r["outcome"] == "stop" and abs(r["result_r"] + 1.0) < 1e-9


def test_buy_fill_then_open_when_no_exit():
    bars = [_bar("d0", 101, 102, 99, 101), _bar("d1", 101, 105, 96, 105)]  # 約定後 stop/target未到達
    r = tk.resolve_outcome(BUY, bars)
    assert r["outcome"] == "open" and r["resolved_date"] is None      # 非終端
    assert abs(r["result_r"] - (105 - 100) / 10) < 1e-9


def test_buy_unfilled_window_elapsed_is_expired():
    bars = [_bar(f"d{i}", 105, 106, 101, 105) for i in range(5)]      # 5本とも low>100 → 未約定・窓満了
    r = tk.resolve_outcome(BUY, bars, expiry=5)
    assert r["fill_status"] == "expired" and r["resolved_date"] == "d4"


def test_buy_unfilled_window_not_elapsed_is_pending():
    bars = [_bar("d0", 105, 106, 101, 105)]   # 1本のみ・未約定・窓未経過(expiry=5)
    r = tk.resolve_outcome(BUY, bars, expiry=5)
    assert r["fill_status"] == "pending" and r["resolved_date"] is None


def test_generation_day_empty_bars_is_pending():
    assert tk.resolve_outcome(BUY, [])["fill_status"] == "pending"     # 生成当日 future=[]


def test_no_same_bar_exit_after_fill():
    # bar0 が約定(low99<=100)かつ stop(low<=90)も同足ヒットだが、決済は fill_i+1 から → 同日決済しない
    bars = [_bar("d0", 101, 102, 88, 95)]      # bar0 low88<=90 だが約定足ゆえ無視
    r = tk.resolve_outcome(BUY, bars, expiry=5)
    assert r["outcome"] == "open"              # 決済走査対象バー無し → open(非終端)


def test_sell_symmetric_target():
    sell = {"direction": "sell", "limit_price": 100.0, "stop_price": 110.0, "target_price": 60.0}
    bars = [_bar("d0", 99, 101, 98, 99),       # bar0 high101>=100 → 約定(sell)
            _bar("d1", 70, 72, 59, 60)]         # bar1 low59<=60 → target
    r = tk.resolve_outcome(sell, bars)
    assert r["outcome"] == "target" and abs(r["result_r"] - 4.0) < 1e-9  # (100-60)/(110-100)


def test_na_cases():
    assert tk.resolve_outcome({"direction": "neutral"}, [])["fill_status"] == "n/a"
    assert tk.resolve_outcome({"direction": "buy", "limit_price": 100, "stop_price": None,
                               "target_price": 140}, [])["fill_status"] == "n/a"
    bad = {"direction": "buy", "limit_price": 90.0, "stop_price": 100.0, "target_price": 140.0}  # limit<=stop
    assert tk.resolve_outcome(bad, [])["fill_status"] == "n/a"   # risk<=0


def test_plan_type():
    assert tk.plan_type("buy", "risk_on") == "risk_on:buy"
    assert tk.plan_type("buy", None) == "unknown:buy"


def test_aggregate_excludes_na_and_uses_terminal():
    rows = [
        {"plan_type": "risk_on:buy", "fill_status": "filled", "outcome": "target", "result_r": 4.0, "days_held": 3},
        {"plan_type": "risk_on:buy", "fill_status": "filled", "outcome": "stop", "result_r": -1.0, "days_held": 2},
        {"plan_type": "risk_on:buy", "fill_status": "pending", "outcome": None, "result_r": None, "days_held": None},
        {"plan_type": "neutral:none", "fill_status": "n/a", "outcome": None, "result_r": None, "days_held": None},
    ]
    agg = {a["type"]: a for a in tk.aggregate_performance(rows)}
    assert "neutral:none" not in agg                          # n/a 除外(R1)
    a = agg["risk_on:buy"]
    assert a["n_plans"] == 3 and a["n_filled"] == 2           # pending も n_plans に含む・n/a 除外
    assert abs(a["fill_rate"] - 2 / 3) < 1e-9
    assert a["n_resolved"] == 2 and abs(a["win_rate"] - 50.0) < 1e-9
    assert abs(a["avg_r"] - 1.5) < 1e-9 and abs(a["avg_days"] - 2.5) < 1e-9
```

- [ ] **Step 2: 失敗を確認** — `backend/venv/bin/python -m pytest backend/test_tracking.py -q` → FAIL（`No module named tracking`）

- [ ] **Step 3: 実装** — `backend/tracking.py`（spec §4.1 準拠）:

```python
"""作戦(daily_plan)の実結果を将来 OHLC から判定する純関数（DB/ネット非依存）。打ち手11。"""
from __future__ import annotations

EXPIRY_DEFAULT = 5


def _num(x):
    try:
        v = float(x)
        return v if v == v else None          # nan→None
    except (TypeError, ValueError):
        return None


def resolve_outcome(plan: dict, future_bars: list[dict], expiry: int = EXPIRY_DEFAULT) -> dict:
    """plan(direction/limit_price/stop_price/target_price) と plan_date 当日含む以降の OHLC
    (future_bars=[{date,open,high,low,close}] 昇順) から結果判定（buy/sell対応・例外を投げない）。"""
    na = {"fill_status": "n/a", "outcome": None, "exit_price": None,
          "result_r": None, "days_held": None, "resolved_date": None}
    direction = plan.get("direction")
    limit, stop, target = _num(plan.get("limit_price")), _num(plan.get("stop_price")), _num(plan.get("target_price"))
    if direction not in ("buy", "sell") or None in (limit, stop, target):
        return na
    is_buy = direction == "buy"
    risk = (limit - stop) if is_buy else (stop - limit)
    if not (risk > 0):
        return na
    n = len(future_bars)
    # 1) 約定（i in [0,expiry)・buy:low<=limit / sell:high>=limit）
    fill_i = None
    for i in range(min(expiry, n)):
        lo, hi = _num(future_bars[i].get("low")), _num(future_bars[i].get("high"))
        if (is_buy and lo is not None and lo <= limit) or ((not is_buy) and hi is not None and hi >= limit):
            fill_i = i
            break
    if fill_i is None:
        if n >= expiry:                        # 窓満了・未約定 → expired(terminal)
            return {**na, "fill_status": "expired", "resolved_date": str(future_bars[expiry - 1].get("date"))}
        return {**na, "fill_status": "pending"}  # 窓未経過 → 非終端
    entry = limit
    # 2) 決済（fill_i+1 以降・優先順 stop>target）
    for j in range(fill_i + 1, n):
        lo, hi = _num(future_bars[j].get("low")), _num(future_bars[j].get("high"))
        hit_stop = (lo is not None and lo <= stop) if is_buy else (hi is not None and hi >= stop)
        hit_tgt = (hi is not None and hi >= target) if is_buy else (lo is not None and lo <= target)
        if hit_stop:
            r = (stop - entry) / risk if is_buy else (entry - stop) / risk
            return {"fill_status": "filled", "outcome": "stop", "exit_price": stop,
                    "result_r": r, "days_held": j - fill_i, "resolved_date": str(future_bars[j].get("date"))}
        if hit_tgt:
            r = (target - entry) / risk if is_buy else (entry - target) / risk
            return {"fill_status": "filled", "outcome": "target", "exit_price": target,
                    "result_r": r, "days_held": j - fill_i, "resolved_date": str(future_bars[j].get("date"))}
    # 未決済 → open(非終端・時価)
    close = _num(future_bars[-1].get("close"))
    r = None if close is None else ((close - entry) / risk if is_buy else (entry - close) / risk)
    return {"fill_status": "filled", "outcome": "open", "exit_price": close,
            "result_r": r, "days_held": (n - 1) - fill_i, "resolved_date": None}


def plan_type(direction, regime) -> str:
    return f"{regime or 'unknown'}:{direction or 'none'}"


def aggregate_performance(rows: list[dict]) -> list[dict]:
    """rows=[{plan_type,fill_status,outcome,result_r,days_held}] を型別集計（n/a 除外・terminal で勝率/R）。"""
    from collections import defaultdict
    g = defaultdict(list)
    for r in rows:
        if r.get("fill_status") == "n/a":
            continue
        g[r.get("plan_type") or "unknown"].append(r)
    out = []
    for typ in sorted(g):
        items = g[typ]
        term = [x for x in items if x.get("outcome") in ("target", "stop")]
        rs = [x["result_r"] for x in term if x.get("result_r") is not None]
        days = [x["days_held"] for x in term if x.get("days_held") is not None]
        n_filled = sum(1 for x in items if x.get("fill_status") == "filled")
        wins = sum(1 for x in term if x.get("outcome") == "target")
        out.append({"type": typ, "n_plans": len(items), "n_filled": n_filled,
                    "fill_rate": n_filled / len(items) if items else None,
                    "n_resolved": len(term),
                    "win_rate": wins / len(term) * 100 if term else None,
                    "avg_r": sum(rs) / len(rs) if rs else None,
                    "avg_days": sum(days) / len(days) if days else None})
    return out
```

- [ ] **Step 4: 通過を確認** — `backend/venv/bin/python -m pytest backend/test_tracking.py -q` → PASS
- [ ] **Step 5: コミット** — `git add backend/tracking.py backend/test_tracking.py && git commit -m "feat: 作戦結果判定の純関数 tracking.py（打ち手11・resolve_outcome/plan_type/aggregate）"`

---

## Task 2: daily_plan 結果列の冪等移行 ＋ upsert_plan に regime 配線

**Files:** Modify `backend/db.py`（`_migrate_daily_plan` L139-147・`upsert_plan` の3箇所）、Test `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く** — `backend/test_api.py` の既存移行テスト群の後に追加:

```python
def test_daily_plan_outcome_columns_and_regime(tmp_path, monkeypatch):
    """daily_plan に結果列が冪等追加され、upsert_plan が regime を保存する（打ち手11）。"""
    import db as dbmod
    dbfile = tmp_path / "tracking.db"
    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()
    cols = {r["name"] for r in dbmod.get_conn().__enter__().execute("PRAGMA table_info(daily_plan)").fetchall()}
    for c in ("regime", "fill_status", "outcome", "exit_price", "result_r", "days_held", "resolved_date"):
        assert c in cols
    dbmod.upsert_plan({"ticker": "7203.T", "plan_date": "2026-06-30", "direction": "buy",
                       "score": 3, "limit_price": 100.0, "stop_price": 90.0, "target_price": 140.0,
                       "regime": "risk_on"})
    row = [p for p in dbmod.list_plans("2026-06-30") if p["ticker"] == "7203.T"][0]
    assert row["regime"] == "risk_on"
    dbmod.init_db()   # 冪等
```

（注: `list_plans` の実シグネチャは既存コードに合わせる。無ければ直接 SELECT。`get_conn().__enter__()` が不格好なら `with dbmod.get_conn() as c:` に展開）

- [ ] **Step 2: 失敗を確認**（regime 列なし or 未保存で FAIL）
- [ ] **Step 3: 実装**
  - `_migrate_daily_plan` の列タプルに追加: `("regime","TEXT"),("fill_status","TEXT"),("outcome","TEXT"),("exit_price","REAL"),("result_r","REAL"),("days_held","INTEGER"),("resolved_date","TEXT")`。
  - `upsert_plan`（INSERT 列リスト・VALUES の `:regime`・ON CONFLICT DO UPDATE SET の3箇所）に `regime` を追加（confidence/shares と同じ配線）。dict に regime が無い呼び出しには `payload.get("regime")` で None 既定。
  - SCHEMA の `CREATE TABLE daily_plan` にも結果列を追記（新規DB用・移行と二重でも冪等）。
- [ ] **Step 4: 通過を確認**
- [ ] **Step 5: コミット** — `git commit -m "feat: daily_plan に結果列を冪等追加＋upsert_plan に regime 配線（打ち手11）"`

---

## Task 3: resolve_plan_outcomes ＋ performance_summary（db.py）

**Files:** Modify `backend/db.py`、Test `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**:

```python
def test_resolve_plan_outcomes_and_summary(tmp_path, monkeypatch):
    """price_data の将来足から作戦を解決し、型別成績を集計。terminal は再解決しない（冪等）。"""
    import db as dbmod, pandas as pd
    dbfile = tmp_path / "resolve.db"
    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()
    dbmod.upsert_plan({"ticker": "X.T", "plan_date": "2026-01-05", "direction": "buy", "score": 3,
                       "limit_price": 100.0, "stop_price": 90.0, "target_price": 140.0, "regime": "risk_on"})
    # 将来 OHLC: 1/5 約定(low99)、1/6 target(high141)
    idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
    df = pd.DataFrame({"open": [101, 120], "high": [102, 141], "low": [99, 119], "close": [101, 140]}, index=idx)
    dbmod.upsert_prices("X.T", df)
    n = dbmod.resolve_plan_outcomes()
    assert n >= 1
    row = [p for p in dbmod.list_plans("2026-01-05") if p["ticker"] == "X.T"][0]
    assert row["outcome"] == "target" and abs(row["result_r"] - 4.0) < 1e-9 and row["resolved_date"]
    summary = dbmod.performance_summary()
    t = [s for s in summary if s["type"] == "risk_on:buy"][0]
    assert t["n_resolved"] == 1 and t["win_rate"] == 100.0
```

- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装**（spec §4.2）:
  - `resolve_plan_outcomes()`: `SELECT * FROM daily_plan WHERE resolved_date IS NULL`。各行: `SELECT date,open,high,low,close FROM price_data WHERE ticker=? AND date>=? ORDER BY date`（plan_date 以降）→ dict list → `tracking.resolve_outcome(plan_row, bars)`。`regime/fill_status/outcome/exit_price/result_r/days_held` を UPDATE。terminal（fill_status in (n/a,expired) or outcome in (target,stop)）のときだけ `resolved_date` を同時 set。terminal 件数を返す。
  - `performance_summary()`: `SELECT regime,direction,fill_status,outcome,result_r,days_held FROM daily_plan` → 各行に `tracking.plan_type(direction,regime)` を付与 → `tracking.aggregate_performance(rows)`。
  - import: `db.py` 冒頭で `import tracking`。
- [ ] **Step 4: 通過を確認**
- [ ] **Step 5: コミット** — `git commit -m "feat: resolve_plan_outcomes＋performance_summary（price_dataから結果解決・打ち手11）"`

---

## Task 4: perform_refresh 配線 ＋ GET /performance

**Files:** Modify `backend/main.py`、Test `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**:

```python
def test_performance_endpoint(client):
    r = client.get("/performance")
    assert r.status_code == 200 and isinstance(r.json()["summary"], list)


def test_refresh_persists_regime(client):
    client.post("/refresh?demo=true")
    # demo は regime None 可。列が読めて例外が出ないことを確認（型別集計が回る）
    assert client.get("/performance").status_code == 200
```

- [ ] **Step 2: 失敗を確認**（/performance 404）
- [ ] **Step 3: 実装**:
  - `perform_refresh` の `upsert_plan({...})` に `"regime": regime` を追加（L449 付近・`regime` は L394 の既存変数）。
  - ループ後・return 前に `try: db.resolve_plan_outcomes()\n    except Exception: pass`（best-effort）。
  - `@app.get("/performance")\ndef performance(): return {"summary": db.performance_summary()}`。
- [ ] **Step 4: 通過を確認** → その後 backend 全スイート（バックグラウンド）`backend/venv/bin/python -m pytest backend/ -q` → 全 PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: perform_refresh で regime 保存＆結果解決＋GET /performance（打ち手11）"`

---

## Task 5: frontend 型別成績ダッシュボード

**Files:** Modify `frontend/src/lib/api.ts`、Create `frontend/src/app/performance/page.tsx`、ナビ導線、Test

- [ ] **Step 1: 失敗するテストを書く** — `api.ts` の純関数 or コンポーネントの最小 test（型別行が描画される・空時メッセージ）。既存 `rows.test.ts` 流儀。
- [ ] **Step 2: 失敗を確認**
- [ ] **Step 3: 実装** — `api.getPerformance()`＋型 `PerfRow`。`app/performance/page.tsx`: 型別成績の表（型・n・fill率%・勝率%・平均R・平均日数）。空配列時「実績が貯まると表示されます」。ナビに「成績」リンク1つ。表示専用。
- [ ] **Step 4: 通過を確認** — `npm --prefix frontend test` → PASS
- [ ] **Step 5: コミット** — `git commit -m "feat: 型別成績ダッシュボード（/performance・打ち手11）"`

---

## Task 6: ドキュメント整合と最終確認

- [ ] **Step 1:** README に「実績トラッキング/成績」の項を1段落追記（任意・他打ち手と同様 changelog 運用なら省略可。判断は entry-fill 時と同じ＝roadmap系は README changelog を更新しない方針なら skip）。
- [ ] **Step 2:** backend 全スイート PASS（既存152＋新規）／frontend 全スイート PASS。
- [ ] **Step 3:** code-review（requesting-code-review）→ 対応 → finishing-a-development-branch で main へ FF マージ＋`git push origin main`。
- [ ] **Step 4:** メモリ [[roadmap-progress]] に「打ち手11 採用済み」を反映。

## 完了基準
- [ ] `tracking.py` 純関数が全ケース（target/stop/open/expired/pending/n/a/sell/同日決済なし）でテスト緑。
- [ ] daily_plan に結果列・regime が保存され、`resolve_plan_outcomes` が price_data から解決・terminal は冪等。
- [ ] `/performance` が型別成績を返し、フロントが表示（空時メッセージ）。
- [ ] backend/frontend 全スイート緑・`DEFAULT_CONFIGS` 不変・既存挙動不変。
- [ ] main へ FF マージ＋push・メモリ更新。

## 実装順の注意
- Task1（純関数・依存なし）→ 2（列/配線）→ 3（解決）→ 4（API）→ 5（UI）。各 Task 後に関連テスト緑を確認、Task4 後に backend 全スイート。
- 生成当日は全 plan が pending（future_bars=[]）＝正常。fill_rate は運用日数が増えて初めて埋まる。
- result_r は提示指値ベースの理論値（コスト未考慮）＝「提示作戦がどう動いたか」の指標（spec §8）。
