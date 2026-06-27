# 非対称R:R出口のライブ採用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 診断で確定した非対称R:R出口（stop 1.5·ATR / target 6·ATR＝R:R 4:1・勝ちを伸ばす）を `atr_exit` config の既定にし、作戦カードとバックテスト双方に反映する（既存DBは非クロバー移行）。

**Architecture:** build_plan は `atr_exit` config の `target_mult`/`stop_mult` を読むだけ（コード不変）。既定値を 1.5→6.0 に変えると、新規DB（シード）・ライブ（perform_refresh→resolve_configs→DB config）・バックテスト（/backtest→DB common config）すべてが新R:Rで動く＝検証=提示を維持。既存DBは冪等・非クロバーの移行で是正。

**Tech Stack:** Python / FastAPI / pandas / pytest（backend, 現状145件）。frontend は無改変（カードは target_price をそのまま表示）。

**Spec:** `docs/superpowers/specs/2026-06-27-asymmetric-rr-exit-design.md`

**前提メモリ:** [[roadmap-progress]]

**テスト実行:** backend `backend/venv/bin/python -m pytest backend/ -q`（cwd 注意・絶対パス推奨）／ frontend `npm --prefix frontend test`

---

## Task 1: 既定 R:R を非対称化（DEFAULT_CONFIGS target_mult 1.5→6.0）

**Files:**
- Modify: `backend/signals.py`（`DEFAULT_CONFIGS` の `atr_exit`・行49付近）
- Test: `backend/test_signals.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_signals.py` の `test_build_plan_exits_are_ordered`（行110付近）の直後に追加:

```python
def test_build_plan_default_rr_is_asymmetric():
    """既定の出口 R:R は非対称（勝ちを伸ばす）: (target-close)/(close-stop) ≈ 4.0（target6/stop1.5）。"""
    df = synthetic_history("TEST.T", n=120, seed=7)   # n≥15 で atr_value 非None
    close = float(df["close"].iloc[-1])
    buy = signals.build_plan(df, "buy", 3)
    rr_buy = (buy["target_price"] - close) / (close - buy["stop_price"])
    assert abs(rr_buy - 4.0) < 1e-6
    sell = signals.build_plan(df, "sell", -3)
    rr_sell = (close - sell["target_price"]) / (sell["stop_price"] - close)
    assert abs(rr_sell - 4.0) < 1e-6
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_build_plan_default_rr_is_asymmetric -q`
Expected: FAIL（現状 R:R=1.0 ＝ `abs(1.0-4.0)<1e-6` が偽）

- [ ] **Step 3: 実装**

`backend/signals.py` の `DEFAULT_CONFIGS` の `atr_exit` 行（行49付近）の `params` の **`"target_mult": 1.5` を `"target_mult": 6.0`** に変更（`stop_mult` は 1.5 のまま、他キーも不変）:
```python
    {"rule_type": "atr_exit", "params": {"length": 14, "stop_mult": 1.5, "target_mult": 6.0, "limit_method": "ma", "limit_ma": 5, "entry_atr_mult": 0.5, "support_n": 20}, "weight": 1, "enabled": 1},
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_build_plan_default_rr_is_asymmetric -q`
Expected: PASS

- [ ] **Step 5: 既存テストの回帰を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（全件）。spec-review で既存テストは順序/相対比較のみで非破壊と確認済みだが、もし DEFAULT_CONFIGS の target_mult 既定に依存した絶対値アサートが見つかったら、その期待値を新R:R（target=close+6·ATR）に合わせて更新する（[[signal-tests-prefer-deterministic-ohlc]] に従い統制OHLC）。破綻が無ければ何もしない。

- [ ] **Step 6: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: 既定の出口R:Rを非対称化（stop1.5/target6=R:R4:1・打ち手9後半・診断由来）"
```

---

## Task 2: 既存DBの非クロバー移行（_migrate_exit_rr）

**Files:**
- Modify: `backend/db.py`（`_migrate_exit_rr` 追加・`init_db` から呼ぶ・行150-156付近）
- Test: `backend/test_api.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_daily_plan_earnings_column_migration`（打ち手10で追加）の直後に追加:

```python
def test_migrate_exit_rr_updates_old_default(tmp_path, monkeypatch):
    """既存DBの atr_exit を旧既定 target_mult=1.5→6.0 に是正（非クロバー・冪等・per-ticker含む）。"""
    import sqlite3, json
    import db as dbmod
    dbfile = tmp_path / "old_rr.db"
    conn = sqlite3.connect(dbfile)
    conn.executescript(dbmod.SCHEMA)   # signal_config 等を作成
    # common 旧既定(1.5)・per-ticker 旧既定(1.5)・per-ticker ユーザー設定(3.0)
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES (NULL,'atr_exit',?,1,1)",
                 (json.dumps({"length": 14, "stop_mult": 1.5, "target_mult": 1.5}),))
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES ('7203.T','atr_exit',?,1,1)", (json.dumps({"target_mult": 1.5}),))
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES ('8306.T','atr_exit',?,1,1)", (json.dumps({"target_mult": 3.0}),))
    conn.commit(); conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()

    cfgs = dbmod.list_configs()
    def tgt(ticker):
        row = [c for c in cfgs if c["ticker"] == ticker and c["rule_type"] == "atr_exit"][0]
        return row["params"].get("target_mult")
    assert tgt(None) == 6.0        # common 旧既定 → 6.0
    assert tgt("7203.T") == 6.0    # per-ticker 旧既定 → 6.0（per-ticker も対象）
    assert tgt("8306.T") == 3.0    # ユーザー設定 → 不変（非クロバー）

    dbmod.init_db()                # 冪等: 再実行で不変
    cfgs2 = dbmod.list_configs()
    common2 = [c for c in cfgs2 if c["ticker"] is None and c["rule_type"] == "atr_exit"][0]
    assert common2["params"]["target_mult"] == 6.0
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_migrate_exit_rr_updates_old_default -q`
Expected: FAIL（`_migrate_exit_rr` 未実装＝target_mult が 1.5 のまま）

- [ ] **Step 3: 実装**

`backend/db.py` に移行関数を追加（`_migrate_daily_plan`・行139付近の隣）:
```python
def _migrate_exit_rr(conn):
    """既存 DB の atr_exit を旧既定 R:R(target_mult=1.5)→新既定(6.0) に是正（冪等・非クロバー）。

    ユーザーが意図的に設定した値（!=1.5）は変えない。common・per-ticker 両方が対象。
    1.5 は IEEE-754 で厳密表現でき JSON を往復しても == 1.5 が安定（spec-review 確認済み）。
    """
    rows = conn.execute(
        "SELECT id, params FROM signal_config WHERE rule_type = 'atr_exit'").fetchall()
    for r in rows:
        params = json.loads(r["params"] or "{}")
        if params.get("target_mult") == 1.5:
            params["target_mult"] = 6.0
            conn.execute("UPDATE signal_config SET params = ? WHERE id = ?",
                         (json.dumps(params), r["id"]))
```
`init_db`（行150-156付近）の `get_conn()` ブロック内、`_migrate_daily_plan(conn)` 呼び出し（行156付近）の直後に **`_migrate_exit_rr(conn)`** を追加（同一コネクション内・`_migrate_daily_plan` と並べる）。`db.py` は既に `import json` 済み。

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_migrate_exit_rr_updates_old_default -q`
Expected: PASS

- [ ] **Step 5: backend 全体を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（既存145 + Task1新規1 + 本タスク新規1 ≈ 147件・件数は目安）

- [ ] **Step 6: コミット**

```bash
git add backend/db.py backend/test_api.py
git commit -m "feat: 既存DBの atr_exit を新R:Rへ非クロバー移行（_migrate_exit_rr・打ち手9後半）"
```

---

## Task 3: ドキュメント整合と最終確認

**Files:**
- Modify: `README.md`（出口R:Rの記述があれば）

- [ ] **Step 1: README の出口R:R記述を確認・更新**

Run: `grep -n "R:R\|1.5\|利確\|target\|ATR" README.md | head -30`
README に「ATR 1.5倍の利確/損切」「R:R 1:1」等、旧固定R:Rを説明する箇所があれば、**stop 1.5·ATR / target 6·ATR（R:R 4:1・勝ちを伸ばす設計）** に更新する。無ければ変更不要（スキップ）。
（注: `課題.md` や `docs/superpowers/specs/*` の過去ドキュメントは履歴として変更しない。更新対象はユーザー向け README のみ。）

- [ ] **Step 2: backend / frontend 全テストを最終確認**

Run: `backend/venv/bin/python -m pytest backend/ -q` → PASS（全件）
Run: `npm --prefix frontend test` → PASS（19+打ち手10で23件・本ブランチはフロント無改変ゆえ不変）

- [ ] **Step 3: コミット（READMEを更新した場合のみ）**

```bash
git add README.md
git commit -m "docs: README の出口R:R記述を非対称R:R（4:1）に更新（打ち手9後半）"
```
（README 変更が無ければコミット不要・このタスクは確認のみで完了）

---

## 完了基準

- [ ] backend 全テスト PASS（既存145＋新規2）／frontend 23件不変
- [ ] 既定 R:R が非対称（build_plan default で target/stop 距離比 ≈4:1）
- [ ] 既存DBが `init_db` で旧既定1.5→6.0 に是正され、ユーザー設定値は不変（冪等）
- [ ] `DEFAULT_CONFIGS` 14件不変（`test_api` 件数アサート維持）
- [ ] 最終 code-review → finishing-a-development-branch で main へ FF マージ＋`git push origin main`

## 実装順の注意

- Task 1（既定値）→ Task 2（移行）の順。Task 1 で全スイートが緑であることを確認してから Task 2 へ。
- フロントは無改変（カードは `target_price` を表示するだけ＝既定値変更で自動的に遠い利確目安になる）。
- 経験的基礎は5銘柄/3y＝方向性。target_mult は config 可変ゆえ、銘柄を増やして再検証できる（spec §8）。
