# 入口の取り逃し是正（押し目を浅く）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 診断で確定した浅い押し目入口（`method="atr"・entry_atr_mult=0.25`＝0.25·ATR の押し目）を `atr_exit` config の既定にし、作戦カードとバックテスト双方に反映する（既存DBは非クロバー・一度きり移行）。fill_rate 37→67%・OOS pnl 8.8→16.5% の前進。

**Architecture:** build_plan は `atr_exit` config の `limit_method`/`entry_atr_mult` を読むだけ（コード不変）。既定を ma/0.5→atr/0.25 に変えると、新規DB（シード）・ライブ（perform_refresh→DB config）・バックテスト（/backtest→DB common）すべてが新入口で動く＝検証=提示を維持。既存DBは冪等・非クロバー・一度きり（app_meta フラグ）の移行で是正。出口R:R移行(`_migrate_exit_rr_once`)と同型・独立フラグ。

**Tech Stack:** Python / FastAPI / pandas / pytest（backend, 現状148件）。frontend は無改変（カードは limit_price/rationale をそのまま表示）。

**Spec:** `docs/superpowers/specs/2026-06-27-entry-pullback-fill-design.md`

**前提メモリ:** [[roadmap-progress]]

**テスト実行:** backend `backend/venv/bin/python -m pytest backend/ -q`（cwd 注意・絶対パス推奨）／ frontend `npm --prefix frontend test`

---

## Task 1: 既定の入口を浅い押し目に（DEFAULT_CONFIGS limit_method ma→atr・entry_atr_mult 0.5→0.25）

**Files:**
- Modify: `backend/signals.py`（`DEFAULT_CONFIGS` の `atr_exit`・行49付近）
- Test: `backend/test_signals.py`（`test_build_plan_default_rr_is_asymmetric` 行128付近の直後）

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_signals.py` の `test_build_plan_default_rr_is_asymmetric` の直後に追加:

```python
def test_build_plan_default_entry_is_shallow_atr():
    """既定の入口指値は 0.25·ATR の浅い押し目（method=atr）。伸びる玉の取り逃しを減らす（診断由来）。

    buy: limit = close − 0.25·ATR ／ sell: limit = close + 0.25·ATR（method=atr は現値キャップ無し）。
    """
    df = synthetic_history("TEST.T", n=120, seed=7)   # n≥15 で atr_value 非None・決定論
    close = float(df["close"].iloc[-1])
    atr = signals.atr_value(df, 14)
    buy = signals.build_plan(df, "buy", 3)
    assert abs(buy["limit_price"] - (close - 0.25 * atr)) < 1e-6
    sell = signals.build_plan(df, "sell", -3)
    assert abs(sell["limit_price"] - (close + 0.25 * atr)) < 1e-6
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_build_plan_default_entry_is_shallow_atr -q`
Expected: FAIL（現状 method=ma ＝ limit=min(5MA,close) ≠ close−0.25·ATR）

- [ ] **Step 3: 実装**

`backend/signals.py` の `DEFAULT_CONFIGS` の `atr_exit` 行（行49付近）の `params` で **`"limit_method": "ma"` を `"limit_method": "atr"`**、**`"entry_atr_mult": 0.5` を `"entry_atr_mult": 0.25`** に変更（他キー＝length/stop_mult/target_mult/limit_ma/support_n は不変）:
```python
    {"rule_type": "atr_exit", "params": {"length": 14, "stop_mult": 1.5, "target_mult": 6.0, "limit_method": "atr", "limit_ma": 5, "entry_atr_mult": 0.25, "support_n": 20}, "weight": 1, "enabled": 1},
```

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_signals.py::test_build_plan_default_entry_is_shallow_atr -q`
Expected: PASS

- [ ] **Step 5: 既存テストの回帰を確認（comment 更新含む）**

`backend/test_signals.py` の `test_build_plan_exits_are_ordered`（行110付近）内のコメント（行116付近）「**既定の買い指値（5日線方式）**…」は既定が atr になると陳腐化する。コメントのみ「**既定の買い指値（atr 浅押し方式）**」に更新（アサーション `limit_price <= close` は atr 方式でも成立＝不変）。

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（全件）。spec-review で既存テスト破綻なしを確認済み（新規1件追加のみ）。万一 DEFAULT の limit_method=ma を絶対値で前提するテストが見つかったら新既定（atr/0.25）に合わせて更新。

- [ ] **Step 6: コミット**

```bash
git add backend/signals.py backend/test_signals.py
git commit -m "feat: 既定の入口指値を浅い押し目(atr0.25)に（入口の取り逃し是正・診断由来）"
```

---

## Task 2: 既存DBの非クロバー・一度きり移行（_migrate_entry_method_once）

**Files:**
- Modify: `backend/db.py`（`_migrate_entry_method`/`_migrate_entry_method_once` を `_migrate_exit_rr_once` 行176の直後に追加・`init_db` 行186の直後で呼ぶ）
- Test: `backend/test_api.py`（`test_migrate_exit_rr_is_one_shot` 行398付近の直後）

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_api.py` の `test_migrate_exit_rr_is_one_shot` の直後に追加:

```python
def test_migrate_entry_method_updates_old_default(tmp_path, monkeypatch):
    """既存DBの atr_exit 入口を旧既定(ma+0.5)→新既定(atr+0.25)に是正（非クロバー・per-ticker含む）。"""
    import sqlite3, json
    import db as dbmod
    dbfile = tmp_path / "old_entry.db"
    conn = sqlite3.connect(dbfile)
    conn.executescript(dbmod.SCHEMA)
    # common 旧既定(ma+0.5)・per-ticker 旧既定(ma+0.5)・per-ticker ユーザー設定(method=support)
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES (NULL,'atr_exit',?,1,1)",
                 (json.dumps({"length": 14, "stop_mult": 1.5, "target_mult": 6.0,
                              "limit_method": "ma", "limit_ma": 5,
                              "entry_atr_mult": 0.5, "support_n": 20}),))
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES ('7203.T','atr_exit',?,1,1)",
                 (json.dumps({"limit_method": "ma", "entry_atr_mult": 0.5}),))
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES ('8306.T','atr_exit',?,1,1)",
                 (json.dumps({"limit_method": "support", "entry_atr_mult": 0.5}),))
    conn.commit(); conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))
    dbmod.init_db()

    cfgs = dbmod.list_configs()
    def params(ticker):
        return [c for c in cfgs if c["ticker"] == ticker and c["rule_type"] == "atr_exit"][0]["params"]
    assert params(None)["limit_method"] == "atr" and params(None)["entry_atr_mult"] == 0.25
    assert params("7203.T")["limit_method"] == "atr" and params("7203.T")["entry_atr_mult"] == 0.25
    assert params("8306.T")["limit_method"] == "support"   # 非クロバー（method!=ma）


def test_migrate_entry_method_is_one_shot(tmp_path, monkeypatch):
    """移行は一度だけ。移行後にユーザーが ma+0.5（深押し）へ戻しても再 init_db で上書きしない。"""
    import sqlite3, json
    import db as dbmod
    dbfile = tmp_path / "entry_oneshot.db"
    conn = sqlite3.connect(dbfile)
    conn.executescript(dbmod.SCHEMA)
    conn.execute("INSERT INTO signal_config (ticker, rule_type, params, weight, enabled) "
                 "VALUES (NULL,'atr_exit',?,1,1)",
                 (json.dumps({"limit_method": "ma", "entry_atr_mult": 0.5}),))
    conn.commit(); conn.close()
    monkeypatch.setattr(dbmod, "DB_PATH", str(dbfile))

    dbmod.init_db()                # 1回目: 旧既定 ma/0.5 → atr/0.25
    atr = [c for c in dbmod.list_configs()
           if c["ticker"] is None and c["rule_type"] == "atr_exit"][0]
    assert atr["params"]["limit_method"] == "atr" and atr["params"]["entry_atr_mult"] == 0.25

    # ユーザーが深押し(ma/0.5)へ意図的に戻す
    dbmod.update_config(atr["id"], params={**atr["params"], "limit_method": "ma", "entry_atr_mult": 0.5})
    dbmod.init_db()                # 2回目: 一度きりなので強制上書きしない
    atr2 = [c for c in dbmod.list_configs()
            if c["ticker"] is None and c["rule_type"] == "atr_exit"][0]
    assert atr2["params"]["limit_method"] == "ma"      # ユーザーの選択が保たれる
    assert atr2["params"]["entry_atr_mult"] == 0.5
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_migrate_entry_method_updates_old_default backend/test_api.py::test_migrate_entry_method_is_one_shot -q`
Expected: FAIL（`_migrate_entry_method` 未実装＝ma/0.5 のまま）

- [ ] **Step 3: 実装**

`backend/db.py` の `_migrate_exit_rr_once`（行176の `}` 直後）に追加:
```python
def _migrate_entry_method(conn):
    """既存 DB の atr_exit 入口を旧既定(ma + entry_atr_mult 0.5)→新既定(atr + 0.25) に是正（冪等・非クロバー）。

    旧既定ペアの行だけ更新。ユーザーが意図的に選んだ method/depth（support 等・depth!=0.5）は変えない。
    0.5/0.25 は IEEE-754 で厳密表現でき JSON を往復しても == が安定（[[asymmetric-rr-exit]] の 1.5 と同様）。
    """
    rows = conn.execute(
        "SELECT id, params FROM signal_config WHERE rule_type = 'atr_exit'").fetchall()
    for r in rows:
        params = json.loads(r["params"] or "{}")
        if params.get("limit_method") == "ma" and params.get("entry_atr_mult") == 0.5:
            params["limit_method"] = "atr"
            params["entry_atr_mult"] = 0.25
            conn.execute("UPDATE signal_config SET params = ? WHERE id = ?",
                         (json.dumps(params), r["id"]))


def _migrate_entry_method_once(conn):
    """`_migrate_entry_method` を app_meta フラグで一度だけ実行する（毎起動の恒久強制にしない）。

    `_migrate_exit_rr_once` と同方針。一度だけにすることで、レガシーDBの旧既定入口だけ是正し、
    以後ユーザーが設定UIで ma/0.5（深押し）を選び直した選択を尊重する（再起動で上書きしない）。
    """
    if conn.execute("SELECT value FROM app_meta WHERE key = 'entry_method_migrated'").fetchone():
        return
    _migrate_entry_method(conn)
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('entry_method_migrated', '1')")
```
`init_db`（行186）の `_migrate_exit_rr_once(conn)` の直後に **`_migrate_entry_method_once(conn)`** を追加（同一コネクション内・`_migrate_exit_rr_once` と並べる）。`db.py` は既に `import json` 済み。

- [ ] **Step 4: テストが通ることを確認**

Run: `backend/venv/bin/python -m pytest backend/test_api.py::test_migrate_entry_method_updates_old_default backend/test_api.py::test_migrate_entry_method_is_one_shot -q`
Expected: PASS

- [ ] **Step 5: backend 全体を確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（既存148 + Task1新規1 + 本タスク新規2 = 151件・件数は目安）

- [ ] **Step 6: コミット**

```bash
git add backend/db.py backend/test_api.py
git commit -m "feat: 既存DBの atr_exit 入口を atr0.25 へ非クロバー一度きり移行（_migrate_entry_method）"
```

---

## Task 3: ドキュメント整合と最終確認

**Files:**
- Modify: `README.md`（入口/提案指値の記述があれば）

- [ ] **Step 1: README の入口記述を確認・更新**

Run: `grep -n "押し目\|指値\|limit\|5日線\|エントリー\|entry" README.md | head -20`
README に「5日線への押し目で指値」等、旧入口を説明する箇所があれば **0.25·ATR の浅い押し目（method=atr）** に更新。無ければスキップ。
（注: `課題.md`・`docs/superpowers/specs/*` の過去ドキュメントは履歴ゆえ変更しない。更新対象はユーザー向け README のみ。）

- [ ] **Step 2: backend / frontend 全テストを最終確認**

Run: `backend/venv/bin/python -m pytest backend/ -q` → PASS（全件）
Run: `npm --prefix frontend test` → PASS（23件・本ブランチはフロント無改変ゆえ不変）

- [ ] **Step 3: コミット（READMEを更新した場合のみ）**

```bash
git add README.md
git commit -m "docs: README の入口記述を浅い押し目(atr0.25)に更新"
```

---

## 完了基準

- [ ] backend 全テスト PASS（既存148＋新規3＝151目安）／frontend 23件不変
- [ ] 既定の入口が浅い押し目（build_plan default で buy limit ≈ close−0.25·ATR・method=atr）
- [ ] 既存DBが `init_db` で旧既定(ma/0.5)→新既定(atr/0.25)に是正され、ユーザー設定値(method=support 等)は不変、移行後に ma/0.5 を選び直しても再起動で上書きされない（一度きり）
- [ ] `DEFAULT_CONFIGS` 14件不変（`test_api` 件数アサート維持）
- [ ] 最終 code-review → finishing-a-development-branch で main へ FF マージ＋`git push origin main`
- [ ] メモリ [[roadmap-progress]] の入口fixを「採用中」→「採用済み（main マージ）」に更新

## 実装順の注意

- Task 1（既定値）→ Task 2（移行）の順。Task 1 で全スイートが緑であることを確認してから Task 2 へ。
- フロントは無改変（カードは `limit_price`/rationale を表示するだけ＝既定値変更で自動的に浅い押し目になる）。
- 経験的基礎は5銘柄/3y・OOS n=58＝方向性。entry_atr_mult は config 可変ゆえ、銘柄を増やして再検証できる（spec §8）。本件は出口R:R 4:1 を固定した上に積む（synergistic＝入口が出口fixの律速だった）。
