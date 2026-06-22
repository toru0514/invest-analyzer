# 打ち手8: リスクベースのサイジング Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 損切り幅と確信度から「1トレードのリスクを一定に保つ株数」を算出し、作戦ボードに推奨株数・想定損失を表示し、バックテスト（plan モード）も同じロジックで約定させる（検証＝提示）。

**Architecture:** 純粋ヘルパ `position_size`（signals.py）を単一の真実とし、作戦ボード（perform_refresh→daily_plan 永続化→フロント表示）とバックテスト（`_run_backtest_plan` のバケット内サイジング、バケット現金を上限にキャップ）の両方から呼ぶ。整数 score/direction・確信度は不変。後方互換は `risk_pct` のキーワード既定と新カラム NULL 許容で担保。

**Tech Stack:** Python（FastAPI・pandas・pytest）／ SQLite（冪等マイグレーション）／ Next.js + TypeScript（vitest）

**Spec:** `docs/superpowers/specs/2026-06-21-risk-based-sizing-design.md`

**前提コマンド:**
- バックエンド全体: `backend/venv/bin/python -m pytest backend/ -q`（現状 111 件）
- 個別: `backend/venv/bin/python -m pytest backend/test_signals.py -q` 等
- フロント: `npm --prefix frontend test`（現状 13 件）

---

## ファイル構成（どのファイルが何を担うか）

| ファイル | 役割 | 変更 |
|---|---|---|
| `backend/signals.py` | `position_size` 純粋ヘルパ＋定数 `DEFAULT_RISK_PCT`/`CONF_FLOOR` | Modify（`build_plan` 付近・定数は VOL_BOOST 群の付近） |
| `backend/db.py` | `daily_plan` に `shares`/`risk_amount` 列・冪等マイグレーション・`upsert_plan` 配線 | Modify |
| `backend/main.py` | 設定（account_size/risk_pct）・`perform_refresh` のサイジング・/backtest・/optimize 配線 | Modify |
| `backend/backtest.py` | `_run_backtest_plan` のリスクサイジング・`run_backtest` の `risk_pct` 素通し | Modify |
| `backend/evaluation.py` | `evaluate_holdout` の `risk_pct` 貫通（benchmark は対象外） | Modify |
| `frontend/src/lib/api.ts` | `PlanRow`/`AppSettings` 型に新フィールド | Modify |
| `frontend/src/lib/rows.ts` | サイジング表示の純関数（`riskSummary`）＋テスト | Modify |
| `frontend/src/app/plan/page.tsx` | `PlanCard` の buy 枝に推奨株数・投資額・想定損失を表示 | Modify |
| `frontend/src/app/settings/page.tsx` | account_size/risk_pct の入力 UI | Modify |
| 各 `test_*.py` / `*.test.ts` | TDD のテスト | Modify |

---

## Task 1: `position_size` 純粋ヘルパ（核）

**Files:**
- Modify: `backend/signals.py`（定数は `VOL_BOOST` 群 `backend/signals.py:257-260` 付近、関数は `build_plan` の直後 `backend/signals.py:673` 付近）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_signals.py` の末尾に追記:

```python
def test_position_size_basic():
    from signals import position_size
    r = position_size(entry=1000.0, stop=950.0, account=1_000_000.0, risk_pct=1.0)
    assert r["risk_per_share"] == 50.0
    assert r["risk_amount"] == 10_000.0          # 口座100万 × 1%
    assert r["shares"] == 200.0                   # 10000 / 50
    assert r["position_value"] == 200_000.0       # 200 × 1000
    assert r["effective_risk_pct"] == 1.0


def test_position_size_confidence_scales_down_only():
    from signals import position_size
    base = position_size(1000.0, 950.0, 1_000_000.0, 1.0)["shares"]
    c0 = position_size(1000.0, 950.0, 1_000_000.0, 1.0, confidence=0)
    c50 = position_size(1000.0, 950.0, 1_000_000.0, 1.0, confidence=50)
    c100 = position_size(1000.0, 950.0, 1_000_000.0, 1.0, confidence=100)
    assert c0["shares"] == 100.0                  # eff=0.5% → 半分
    assert c50["shares"] == 150.0                 # eff=0.75%
    assert c100["shares"] == base == 200.0        # eff=1.0%（基準を超えない）


def test_position_size_scales_with_stop_width():
    from signals import position_size
    wide = position_size(1000.0, 900.0, 1_000_000.0, 1.0)   # 損切幅100
    narrow = position_size(1000.0, 975.0, 1_000_000.0, 1.0) # 損切幅25
    assert wide["shares"] == 100.0                 # 広い→小さく
    assert narrow["shares"] == 400.0               # 狭い→大きく


def test_position_size_guards_return_zero():
    from signals import position_size
    for args in [
        (1000.0, 1000.0, 1_000_000.0, 1.0),   # risk_per_share = 0
        (1000.0, 1050.0, 1_000_000.0, 1.0),   # entry < stop（buy で異常）
        (1000.0, 950.0, 0.0, 1.0),            # account 0
        (1000.0, 950.0, 1_000_000.0, 0.0),    # risk_pct 0
        (None, 950.0, 1_000_000.0, 1.0),      # None
    ]:
        r = position_size(*args)
        assert r["shares"] == 0.0 and r["risk_amount"] == 0.0
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k position_size`
Expected: FAIL（`ImportError: cannot import name 'position_size'`）

- [ ] **Step 3: 最小実装**

`backend/signals.py:257-260`（`VOL_BOOST` 群）に定数追加:

```python
DEFAULT_RISK_PCT = 1.0   # 1トレード許容リスク（口座に対する%。1.0 = 1%）
CONF_FLOOR = 0.5         # 確信度0で許容リスクを基準の何倍まで縮めるか（自信が低い時だけ縮小）
```

`backend/signals.py` の `build_plan` 直後（`:673` の `return out` の後・関数外）に追加:

```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k position_size`
Expected: PASS（4 件）

- [ ] **Step 5: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: リスクベースのサイジング純粋ヘルパ position_size を追加（打ち手8）"
```

---

## Task 2: `daily_plan` に shares/risk_amount（スキーマ・マイグレーション・upsert）

**Files:**
- Modify: `backend/db.py`（CREATE TABLE `:94` 付近・`_migrate_daily_plan` `:139-142`・`upsert_plan` `:413-432`）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の末尾に追記（既存 DB 互換の冪等マイグレーション＋upsert ラウンドトリップ）:

```python
def test_daily_plan_sizing_columns_and_upsert(tmp_path, monkeypatch):
    import importlib, sqlite3
    dbfile = tmp_path / "old.db"
    # shares/risk_amount 列が無い旧 daily_plan を作る
    conn = sqlite3.connect(dbfile)
    conn.execute("CREATE TABLE daily_plan (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "ticker TEXT NOT NULL, plan_date TEXT NOT NULL, direction TEXT, score INTEGER, "
                 "limit_price REAL, stop_price REAL, target_price REAL, rationale TEXT, "
                 "UNIQUE(ticker, plan_date))")
    conn.execute("INSERT INTO daily_plan (ticker, plan_date, direction, score) "
                 "VALUES ('OLD.T','2026-06-01','buy',3)")
    conn.commit(); conn.close()

    monkeypatch.setenv("DB_PATH", str(dbfile))
    import db as dbmod; importlib.reload(dbmod)
    dbmod.init_db()                                   # 冪等マイグレーションで列追加
    cols = {r["name"] for r in dbmod.get_conn().execute("PRAGMA table_info(daily_plan)")}
    assert {"shares", "risk_amount"} <= cols
    # 旧行は NULL のまま読める
    old = [r for r in dbmod.list_plan("2026-06-01") if r["ticker"] == "OLD.T"][0]
    assert old["shares"] is None and old["risk_amount"] is None
    # 新規 upsert で値が入る
    dbmod.upsert_plan({"ticker": "NEW.T", "plan_date": "2026-06-01", "direction": "buy",
                       "score": 3, "limit_price": 1000.0, "stop_price": 950.0,
                       "target_price": 1100.0, "rationale": "x",
                       "confidence": 70.0, "shares": 200.0, "risk_amount": 10000.0})
    new = [r for r in dbmod.list_plan("2026-06-01") if r["ticker"] == "NEW.T"][0]
    assert new["shares"] == 200.0 and new["risk_amount"] == 10000.0
    importlib.reload(dbmod)                           # 後始末（DB_PATH 戻しは monkeypatch が担う）
```

> 注: `DB_PATH` 環境変数で DB ファイルを差し替える既存の仕組みがあるか `db.py` 冒頭で確認すること。無ければ `db.get_conn` の接続先解決を読んで、テストの DB 差し替え方法を合わせる（既存テストの fixture `client` の作り方＝`test_api.py` 冒頭を参照）。

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k daily_plan_sizing`
Expected: FAIL（列 `shares`/`risk_amount` が無い、または upsert が新キーを書けない）

- [ ] **Step 3: 最小実装**

`backend/db.py` の `CREATE TABLE ... daily_plan` の `confidence REAL,` 行（`:94`）の直後に追加:

```sql
  shares        REAL,
  risk_amount   REAL,
```

`_migrate_daily_plan`（`:139-142`）の追加列タプルに2列追加:

```python
    for col, decl in (("ai_summary", "TEXT"), ("ai_confidence", "INTEGER"),
                      ("ai_risks", "TEXT"), ("confidence", "REAL"),
                      ("shares", "REAL"), ("risk_amount", "REAL")):
```

`upsert_plan`（`:413`）の `setdefault` preserve 列に2つ追加:

```python
    for k in ("ai_summary", "ai_confidence", "ai_risks", "confidence", "shares", "risk_amount"):
        row.setdefault(k, None)
```

`upsert_plan` の INSERT 列・VALUES・`ON CONFLICT DO UPDATE SET` に2列追加（`:417-431`）。具体的には:
- 列リストの `confidence,` の後に `shares, risk_amount,`
- VALUES の `:confidence,` の後に `:shares, :risk_amount,`
- DO UPDATE SET に `confidence=excluded.confidence, shares=excluded.shares, risk_amount=excluded.risk_amount,`

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k daily_plan_sizing`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/db.py backend/test_api.py
git commit -m "feat: daily_plan に shares/risk_amount 列と冪等マイグレーション・upsert 配線（打ち手8）"
```

---

## Task 3: 設定 account_size / risk_pct（main.py）

**Files:**
- Modify: `backend/main.py`（`SettingsUpdate` `:135-142`・`_safe_top_n` 付近 `:145`・`get_settings` `:155-166`・`put_settings` `:169-185`）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py::test_settings_get_and_update` の最後（既定に戻す行の前）に追記:

```python
    # 打ち手8: account_size / risk_pct（既定 100万 / 1.0%）
    assert s["account_size"] == 1_000_000.0
    assert s["risk_pct"] == 1.0
    client.put("/settings", json={"account_size": 2_000_000, "risk_pct": 0.5})
    s3 = client.get("/settings").json()
    assert s3["account_size"] == 2_000_000.0 and s3["risk_pct"] == 0.5
    client.put("/settings", json={"risk_pct": 0})       # 範囲外（0）→既定へ
    assert client.get("/settings").json()["risk_pct"] == 1.0
    client.put("/settings", json={"risk_pct": 150})     # 範囲外（>100）→既定へ
    assert client.get("/settings").json()["risk_pct"] == 1.0
    client.put("/settings", json={"account_size": -5})  # 範囲外（負）→既定へ
    assert client.get("/settings").json()["account_size"] == 1_000_000.0
    client.put("/settings", json={"account_size": 1_000_000, "risk_pct": 1.0})  # 後始末
```

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k settings_get_and_update`
Expected: FAIL（`KeyError: 'account_size'`）

- [ ] **Step 3: 最小実装**

`_safe_top_n`（`:145`）の直後にヘルパ追加:

```python
def _safe_pos_float(raw, default: float, max_value: float | None = None) -> float:
    """正の float。範囲外（非正・上限超・非数）は default にフォールバック（_safe_top_n と同方針）。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v <= 0 or (max_value is not None and v > max_value):
        return default
    return v
```

`SettingsUpdate`（`:135-142`）に2フィールド追加:

```python
    account_size: Optional[float] = None
    risk_pct: Optional[float] = None
```

`get_settings`（`:158-166` の返す dict）に2キー追加:

```python
        "account_size": _safe_pos_float(m.get("account_size", "1000000"), 1_000_000.0),
        "risk_pct": _safe_pos_float(m.get("risk_pct", "1.0"), 1.0, max_value=100.0),
```

`put_settings`（`:183` の top_n 配線の後）に追加:

```python
    if payload.account_size is not None:
        db.set_meta("account_size", str(_safe_pos_float(payload.account_size, 1_000_000.0)))
    if payload.risk_pct is not None:
        db.set_meta("risk_pct", str(_safe_pos_float(payload.risk_pct, 1.0, max_value=100.0)))
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k settings_get_and_update`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: 設定に account_size/risk_pct を追加（範囲外は既定フォールバック・打ち手8）"
```

---

## Task 4: perform_refresh で buy プランをサイジングして永続化（main.py）

**Files:**
- Modify: `backend/main.py`（imports `:26-35`・`perform_refresh` `:341` のサイジング算出と `upsert_plan` `:409-418`）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` 末尾に追記（demo refresh 後、buy プランに shares/risk_amount が入り、非 buy は None）:

```python
def test_plan_has_risk_sizing_for_buy(client):
    client.post("/watchlist", json={"ticker": "7203.T", "name": "トヨタ"})
    client.post("/refresh?demo=true")
    rows = client.get("/plan").json()["rows"]
    assert rows, "プラン行が無い"
    for r in rows:
        if r["direction"] == "buy" and r["limit_price"] and r["stop_price"]:
            assert isinstance(r["shares"], (int, float)) and r["shares"] >= 0
            assert isinstance(r["risk_amount"], (int, float)) and r["risk_amount"] >= 0
        else:
            assert r["shares"] is None and r["risk_amount"] is None
```

> 注: 既存の demo refresh テスト（`test_refresh_signals_and_prices` など `test_api.py:131` 付近）の watchlist 準備・client fixture の流儀に合わせること。demo データで buy が一切出ない場合に備え、少なくとも「全行で shares/risk_amount キーが存在し型が `float|None`」を必ず検証する（buy 不在でも落ちない）。

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k plan_has_risk_sizing`
Expected: FAIL（`KeyError: 'shares'` か、buy 行で shares が None のまま）

- [ ] **Step 3: 最小実装**

`backend/main.py` の signals インポート（`:26-35`）に `position_size` を追加。

`perform_refresh`（`:341`）の冒頭、設定取得（`buy_th, sell_th = db.get_thresholds()` `:348` 付近）の近くで account_size/risk_pct を取得:

```python
    settings = db.get_all_meta()
    account_size = _safe_pos_float(settings.get("account_size", "1000000"), 1_000_000.0)
    risk_pct = _safe_pos_float(settings.get("risk_pct", "1.0"), 1.0, max_value=100.0)
```

`plan = build_plan(...)`（`:394`）の直後に、buy のみサイジング:

```python
        if direction == "buy" and plan["limit_price"] and plan["stop_price"]:
            sz = position_size(plan["limit_price"], plan["stop_price"],
                               account_size, risk_pct, confidence=detail.get("confidence"))
            plan_shares, plan_risk = sz["shares"], sz["risk_amount"]
        else:
            plan_shares = plan_risk = None
```

`db.upsert_plan({...})`（`:409-418`）の dict に2キー追加:

```python
            "shares": plan_shares, "risk_amount": plan_risk,
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py -q -k plan_has_risk_sizing`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/main.py backend/test_api.py
git commit -m "feat: perform_refresh で buy プランをリスクサイジングし daily_plan に永続化（打ち手8）"
```

---

## Task 5: `_run_backtest_plan` のリスクサイジング＋`run_backtest` 素通し（backtest.py）

**Files:**
- Modify: `backend/backtest.py`（import `:13`・`run_backtest` シグネチャ `:36-40` と plan 分岐 `:53-56`・`_run_backtest_plan` シグネチャと約定ブロック）
- Test: `backend/test_backtest.py`（既存 `test_run_backtest_plan_rs_invariant` `:163-175` を書き直し＋新規）

- [ ] **Step 1: 失敗するテスト（と既存テストの書き直し）**

まず既存 `test_run_backtest_plan_rs_invariant`（`:163-175`）を**約定構造の不変＋価格の不変**に置き換え（pnl 等価は削除）:

```python
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
    # 約定価格列（買い/売りの fill）は RS 非依存
    base_prices = [round(t["price"], 6) for t in base["trades"]]
    rs_prices = [round(t["price"], 6) for t in rs["trades"]]
    assert base_prices == rs_prices
```

新規テスト（サイジングが損切り幅でスケール／キャップ縮退）を追記:

```python
def test_run_backtest_plan_risk_sizes_by_stop_width():
    """大きめ資本で、risk_pct が小さいほど建玉（株数）が小さくなる＝リスクサイジングが効く。"""
    from market import synthetic_history
    import backtest
    hist = {"P.T": synthetic_history("P.T", n=160, seed=3)}
    big = dict(configs=None, exit_mode="plan", backtest_days=60, buy_threshold=2,
               sell_threshold=-2, initial_capital=50_000_000)   # キャップに張り付かない大資本
    small_risk = backtest.run_backtest(hist, risk_pct=0.2, **big)
    large_risk = backtest.run_backtest(hist, risk_pct=2.0, **big)
    buys_small = sum(t["shares"] for t in small_risk["trades"] if t["action"] == "buy")
    buys_large = sum(t["shares"] for t in large_risk["trades"] if t["action"] == "buy")
    if buys_small > 0 or buys_large > 0:
        assert buys_large > buys_small        # リスク許容が大きいほど株数が多い


def test_run_backtest_plan_caps_at_bucket_cash():
    """小資本では desired がバケットを超え、全力買い（投資額 ≈ バケット現金）に縮退する。"""
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
```

> 注: 上の数値（資本額・seed）は `synthetic_history` の価格レンジ次第で buy が出ない/常にキャップに入る可能性がある。Step 4 で実値を確認し、`buys_large > buys_small` が観測できる資本・risk_pct に調整すること（spec §6.4 観測可能性）。観測できないときは資本を上げる。

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q -k "rs_structure_invariant or risk_sizes or caps_at_bucket"`
Expected: FAIL（`run_backtest() got an unexpected keyword argument 'risk_pct'`）

- [ ] **Step 3: 最小実装**

`backend/backtest.py` の import（`:13`）に追加:

```python
from signals import (BUY_THRESHOLD, DEFAULT_CONFIGS, DEFAULT_RISK_PCT, SELL_THRESHOLD,
                     build_plan, evaluate, position_size)
```

`run_backtest` シグネチャ（`:36-40`）に `risk_pct=DEFAULT_RISK_PCT,` を追加し、plan 分岐（`:53-56`）の `_run_backtest_plan(...)` 呼び出しに `risk_pct=risk_pct` をキーワードで渡す（**忘れると plan で黙って無効**＝打ち手7の落とし穴と同型）。

`_run_backtest_plan` シグネチャに `risk_pct=DEFAULT_RISK_PCT,` を追加。

意思決定ステップ（`backtest.py:217` の `score, direction, _ = evaluate(...)`）を detail 取得に変更し、pending に confidence を保持:

```python
                score, direction, detail = evaluate(window, configs, buy_threshold, sell_threshold,
                                               regime=_regime_at(regime_series, df.index[i]),
                                               rs_strength=_rs_at(index_history, rs_params, window, df.index[i]))
```

`pending = {...}` を組む箇所（`:233` 付近）に confidence を追加:

```python
                        pending = {"limit": plan["limit_price"], "stop": plan["stop_price"],
                                   "target": plan["target_price"], "expires": i + entry_expiry_days,
                                   "confidence": detail.get("confidence")}
```

約定ブロック（`:184-191` 付近、現状 `shares = (cash - fee) / fill` の全力買い）を、全力株数を上限にリスクサイジング:

```python
                    fill = apply_costs(pending["limit"], "buy", cost)
                    fee = commission_cost(cash, cost)
                    affordable = (cash - fee) / fill
                    desired = position_size(pending["limit"], pending["stop"],
                                            initial_capital, risk_pct,
                                            confidence=pending.get("confidence"))["shares"]
                    if desired and 0 < desired < affordable:
                        shares = desired
                        cash -= shares * fill + commission_cost(shares * fill, cost)
                    else:
                        shares = affordable          # キャップ＝従来の全力買い
                        cash = 0.0
                    entry_price, stop, target, entry_i = fill, pending["stop"], pending["target"], i
                    orders_filled += 1
                    trades.append({"date": d, "ticker": ticker, "action": "buy",
                                   "price": fill, "shares": shares})
                    pending = None
```

> 既存の約定ブロックの行（`fee = commission_cost(cash, cost)` / `shares = (cash - fee) / fill` / `cash = 0.0` / `entry_price, stop, ... = ...` / `trades.append(...)` / `pending = None`）を上記で置換する。`orders_filled += 1` の位置は現状に合わせる。

- [ ] **Step 4: テストが通ることを確認（必要なら資本・seed を調整）**

Run: `backend/venv/bin/python -m pytest backend/test_backtest.py -q`
Expected: PASS（既存 backtest テスト＋新規3件）。`risk_sizes_by_stop_width` が観測できなければ Step 1 の注に従い資本を上げる。

- [ ] **Step 5: コミット**

```bash
git add backend/backtest.py backend/test_backtest.py
git commit -m "feat: _run_backtest_plan をリスクサイジング化・run_backtest に risk_pct 素通し（検証=提示・打ち手8）"
```

---

## Task 6: `evaluate_holdout` の risk_pct 貫通＋/backtest・/optimize 配線

**Files:**
- Modify: `backend/evaluation.py`（`evaluate_holdout` シグネチャ `:80-82`・`_bt` `:99-104`）
- Modify: `backend/main.py`（`/backtest` `:511-514`・`/optimize` `:555-557`・必要なら `_risk_pct` ヘルパ）
- Test: `backend/test_evaluation.py`・`backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_evaluation.py` に追記（risk_pct を渡しても完走し、結果キーが揃う）:

```python
def test_evaluate_holdout_accepts_risk_pct():
    from market import synthetic_history
    from evaluation import evaluate_holdout
    hist = {f"P{i}.T": synthetic_history(f"P{i}.T", n=200, seed=i) for i in range(2)}
    res = evaluate_holdout(hist, configs=None, initial_capital=5_000_000, risk_pct=0.5)
    assert "chosen_params" in res and "out_of_sample" in res
```

> 注: `out_of_sample` の正確なキー名は `evaluation.py` の return（`:145` 以降）を確認して合わせる。

- [ ] **Step 2: 失敗を確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py -q -k risk_pct`
Expected: FAIL（`evaluate_holdout() got an unexpected keyword argument 'risk_pct'`）

- [ ] **Step 3: 最小実装**

`backend/evaluation.py` の `evaluate_holdout` シグネチャ（`:80-82`）に `risk_pct=None,` を追加し、冒頭で既定解決:

```python
    from signals import DEFAULT_RISK_PCT
    risk_pct = DEFAULT_RISK_PCT if risk_pct is None else risk_pct
```

`_bt`（`:100-104`）の `run_backtest(...)` 呼び出しに `risk_pct=risk_pct` を追加（benchmark 呼び出し `:136-140` には**足さない**＝score モードのため）。

`backend/main.py` にヘルパ追加（`_safe_pos_float` の近く）:

```python
def _risk_pct() -> float:
    return _safe_pos_float(db.get_all_meta().get("risk_pct", "1.0"), 1.0, max_value=100.0)
```

`/backtest`（`:511`）の `run_backtest(...)` に `risk_pct=_risk_pct()` を追加。
`/optimize`（`:555`）の `evaluate_holdout(...)` に `risk_pct=_risk_pct()` を追加。

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_evaluation.py backend/test_api.py -q`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/evaluation.py backend/main.py backend/test_evaluation.py
git commit -m "feat: evaluate_holdout に risk_pct を貫通し /backtest・/optimize を設定値で配線（打ち手8）"
```

---

## Task 7: フロント PlanRow 型＋作戦カードのサイジング表示

**Files:**
- Modify: `frontend/src/lib/api.ts`（`PlanRow` `:64-81`）
- Modify: `frontend/src/lib/rows.ts`（純関数 `riskSummary` を追加）
- Test: `frontend/src/lib/__tests__/rows.test.ts`
- Modify: `frontend/src/app/plan/page.tsx`（`PlanCard` buy 枝 `:227-235`・`PlanBoard` で account_size を取得 `:48` 付近）

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/lib/__tests__/rows.test.ts` に追記:

```ts
import { riskSummary } from "@/lib/rows";

describe("riskSummary", () => {
  it("株数・投資額・口座%を整形する", () => {
    const r = riskSummary({ shares: 200, risk_amount: 10000, limit_price: 1000 }, 1_000_000);
    expect(r).not.toBeNull();
    expect(r!.shares).toBe(200);
    expect(r!.positionValue).toBe(200000);     // 200 × 1000
    expect(r!.riskPctOfAccount).toBeCloseTo(1.0); // 10000 / 1,000,000
  });
  it("shares が無ければ null", () => {
    expect(riskSummary({ shares: null, risk_amount: null, limit_price: 1000 }, 1_000_000)).toBeNull();
  });
});
```

- [ ] **Step 2: 失敗を確認**

Run: `npm --prefix frontend test -- --run rows`
Expected: FAIL（`riskSummary` 未定義）

- [ ] **Step 3: 最小実装**

`frontend/src/lib/api.ts` の `PlanRow`（`:74` の `target_price` の後あたり）に2フィールド追加:

```ts
  shares: number | null;
  risk_amount: number | null;
```

`frontend/src/lib/rows.ts` に純関数を追加:

```ts
// 作戦カードのサイジング表示（打ち手8）。shares が無い（旧行/非buy）なら null。
export function riskSummary(
  row: { shares: number | null; risk_amount: number | null; limit_price: number | null },
  accountSize: number,
): { shares: number; positionValue: number; riskAmount: number; riskPctOfAccount: number } | null {
  if (row.shares == null || row.risk_amount == null) return null;
  const positionValue = row.limit_price != null ? row.shares * row.limit_price : 0;
  const riskPctOfAccount = accountSize > 0 ? (row.risk_amount / accountSize) * 100 : 0;
  return { shares: row.shares, positionValue, riskAmount: row.risk_amount, riskPctOfAccount };
}
```

`frontend/src/app/plan/page.tsx`:
- `AppSettings` を import 済み。`PlanBoard` に `const [accountSize, setAccountSize] = useState(1_000_000);` を追加し、`load()` の `setTopN(settings.top_n)`（`:48`）の隣で `setAccountSize(settings.account_size);`。
- `PlanCard` 呼び出し（Top N と全リストの2箇所 `:150-158`・`:172-180`）に `accountSize={accountSize}` を渡す。
- `PlanCard` の props 型に `accountSize: number;` を追加。
- buy の指値グリッド（`:229-233`）の直後に表示を追加:

```tsx
          {(() => {
            const s = riskSummary(row!, accountSize);
            return s ? (
              <p className="mt-2 text-xs text-slate-600">
                推奨株数 <span className="font-semibold">{Math.round(s.shares).toLocaleString()}</span> 株
                ／ 投資額 約 {Math.round(s.positionValue).toLocaleString()} 円
                ／ 想定損失 {Math.round(s.riskAmount).toLocaleString()} 円（口座の {s.riskPctOfAccount.toFixed(1)}%）
              </p>
            ) : null;
          })()}
```

- `rows` から `riskSummary` を import（`plan/page.tsx` 冒頭 `import { selectTopN } from "@/lib/rows";` を `import { riskSummary, selectTopN } from "@/lib/rows";` に）。

- [ ] **Step 4: テストが通ることを確認**

Run: `npm --prefix frontend test -- --run rows` → PASS。続けて `npm --prefix frontend run build`（型エラーが無いこと）。

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/rows.ts frontend/src/lib/__tests__/rows.test.ts frontend/src/app/plan/page.tsx
git commit -m "feat: 作戦カードに推奨株数・投資額・想定損失を表示（純関数 riskSummary・打ち手8）"
```

---

## Task 8: フロント AppSettings 型＋設定 UI（account_size / risk_pct）

**Files:**
- Modify: `frontend/src/lib/api.ts`（`AppSettings` `:45-53`）
- Modify: `frontend/src/app/settings/page.tsx`（「スコア閾値・自動更新」セクション `:257-324`）
- Test: 既存 vitest 13 件＋Task 7 の rows（不変であること）＋ビルド

- [ ] **Step 1: 型と UI を追加（UI はユニットテスト対象外＝ビルドで担保）**

`frontend/src/lib/api.ts` の `AppSettings`（`:52` の `top_n` の後）に追加:

```ts
  account_size: number;
  risk_pct: number;
```

`frontend/src/app/settings/page.tsx` の「スコア閾値・自動更新」セクション内（売り閾値の入力 `:277-282` の後）にサイジング入力を追加:

```tsx
            <label className="block text-sm">
              口座資金（円・サイジング基準）
              <input
                type="number"
                value={settings.account_size}
                onChange={(e) => setSettings({ ...settings, account_size: Number(e.target.value) })}
                className="mt-1 w-full rounded border px-2 py-1"
              />
            </label>
            <label className="block text-sm">
              1トレード許容リスク（％・損切り到達時の損失上限の目安）
              <input
                type="number"
                step={0.1}
                value={settings.risk_pct}
                onChange={(e) => setSettings({ ...settings, risk_pct: Number(e.target.value) })}
                className="mt-1 w-full rounded border px-2 py-1"
              />
            </label>
```

> 注: セクションの実際の JSX 構造（`<label>` の class・グリッド）に合わせて体裁を整える。`saveSettings`（`:155`）は `api.updateSettings(settings)` で全 `AppSettings` を送るため、新フィールドは追加配線不要で保存される。

- [ ] **Step 2: ビルドと既存テスト**

Run: `npm --prefix frontend run build`（型エラー無し）
Run: `npm --prefix frontend test -- --run`
Expected: 既存13件＋riskSummary が PASS

- [ ] **Step 3: コミット**

```bash
git add frontend/src/lib/api.ts frontend/src/app/settings/page.tsx
git commit -m "feat: 設定画面に口座資金・許容リスク%の入力を追加（打ち手8）"
```

---

## Task 9: 最終検証（全テスト・回帰確認）

**Files:** なし（検証のみ）

- [ ] **Step 1: バックエンド全テスト**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（111 + 新規 ≈ 9 件前後）。失敗ゼロ。

- [ ] **Step 2: フロント全テスト＋ビルド**

Run: `npm --prefix frontend test -- --run` → 13 + 2（riskSummary）件 PASS
Run: `npm --prefix frontend run build` → 成功

- [ ] **Step 3: 後方互換のスポットチェック**

- score モード backtest が不変: `backend/venv/bin/python -m pytest backend/test_backtest.py -q -k "score or invariant"` を確認。
- 確信度・Top N（打ち手6/7）が不変: `backend/venv/bin/python -m pytest backend/test_signals.py -q -k "confidence or strength or rs"` を確認。
- `DEFAULT_CONFIGS` 件数（14）は変えていない（`test_api.py:85` のアサートが PASS のまま）。

- [ ] **Step 4: 最終コミット（必要なら）**

```bash
git status   # クリーンであることを確認（未コミットがあればまとめてコミット）
```

---

## 完了の定義
- `position_size` が単体テストで損切り幅・確信度・ガードを満たす。
- 作戦ボードの buy 行に推奨株数・投資額・想定損失（¥ と口座%）が出る。
- バックテスト plan モードがリスクサイジングで約定し（検証＝提示）、score モード・benchmark・確信度・Top N・`DEFAULT_CONFIGS` 件数は不変。
- バックエンド・フロントの全テストがグリーン。
