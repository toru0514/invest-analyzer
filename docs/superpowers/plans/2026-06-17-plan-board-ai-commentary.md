# 作戦ボードAI解説（Gemini）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 作戦ボードの各銘柄に、Gemini が生成する「根拠・確信度(0-100)・主なリスク」を日本語で付与して表示する（無料枠・完全オプトイン）。

**Architecture:** backend(Python/FastAPI) に Gemini 呼び出しモジュール `ai_commentary.py` を追加し、`/refresh`（作戦生成）時に各銘柄分を生成して `daily_plan` に保存。地合い(指数トレンド)・決算日も無料データで best-effort 入力。frontend `/plan` のカードに表示。キー未設定・失敗時は従来どおり動作。

**Tech Stack:** Python, FastAPI, SQLite, `google-generativeai`(遅延 import), `python-dotenv`(任意), pytest / Next.js(TypeScript), Vitest。

**設計書:** `docs/superpowers/specs/2026-06-17-plan-board-ai-commentary-design.md`

**重要な設計判断（テスト容易性・無依存起動）:**
- `google.generativeai` の import は `_generate_text()` 内に置く（モジュール読込・単体テストは SDK 未インストールでも通る）。
- `python-dotenv` の import は try/except（未導入でも環境変数で動く）。
- 単体テストは Gemini 呼び出し（`_generate_text`）を monkeypatch し、ネットワーク不要。

---

## File Structure

- Create: `backend/ai_commentary.py` — Gemini解説の生成（設定読込・プロンプト・JSON抽出・検証）
- Create: `backend/test_ai_commentary.py` — 単体テスト（Gemini はモック）
- Modify: `backend/market.py` — `fetch_earnings_days(ticker)` 追加（best-effort）
- Modify: `backend/db.py` — `daily_plan` に3列追加＋既存DBマイグレーション＋`upsert_plan` 拡張
- Modify: `backend/main.py` — `perform_refresh` に 地合い・決算・AI解説 を統合
- Modify: `backend/requirements.txt` — `google-generativeai` / `python-dotenv` 追加
- Modify: `frontend/src/lib/api.ts` — `PlanRow` 型に3フィールド追加
- Modify: `frontend/src/app/plan/page.tsx` — カードに「AI解説」表示
- Create (gitignore済): `backend/.env` — `GEMINI_API_KEY`（listing-text-generator と同じ値）/ `GEMINI_MODEL`

注: `.gitignore` は既に `.env*` を無視済み（確認のみ・変更不要）。

---

## Task 1: backend 依存追加（requirements.txt）

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: requirements.txt に追記**

`backend/requirements.txt` の末尾（`uvicorn==0.49.0` の後）に追加:

```
# 作戦ボードのAI解説（Gemini）。import は実行時のみ・単体テストは未導入でも通る。
google-generativeai==0.8.6
python-dotenv==1.1.1
```

- [ ] **Step 2: コミット**

```bash
git add backend/requirements.txt
git commit -m "$(printf 'chore: AI解説用に google-generativeai / python-dotenv を追加\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: ai_commentary.py — `extract_json`（TDD）

**Files:**
- Create: `backend/ai_commentary.py`
- Test: `backend/test_ai_commentary.py`

- [ ] **Step 1: 失敗するテストを書く**

`backend/test_ai_commentary.py`:

```python
import ai_commentary as ac


def test_extract_json_plain():
    assert ac.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "```json\n{\"confidence\": 70, \"summary\": \"x\"}\n```"
    assert ac.extract_json(text)["confidence"] == 70


def test_extract_json_with_surrounding_text():
    text = 'これはJSONです: {"summary": "ok"} 以上。'
    assert ac.extract_json(text)["summary"] == "ok"


def test_extract_json_no_object_raises():
    import pytest
    with pytest.raises(ValueError):
        ac.extract_json("JSONはありません")
```

- [ ] **Step 2: 失敗を確認**

Run: `cd backend && venv/bin/python -m pytest test_ai_commentary.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'ai_commentary'`）

- [ ] **Step 3: 最小実装**

`backend/ai_commentary.py`:

```python
"""作戦ボードのAI解説（Gemini）。

各銘柄の「根拠・確信度・リスク」を日本語生成する。listing-text-generator と同じ
環境変数（GEMINI_API_KEY / GEMINI_MODEL）を使う。キー未設定・失敗時は None を返し、
作戦ボードは従来どおり動作する（完全オプトイン）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# .env（backend/.env）を読む。未導入でも環境変数があれば動く。
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

DEFAULT_MODEL = "gemini-2.5-flash"


def _api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def _model_name() -> str:
    return os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def extract_json(text: str) -> Any:
    """Gemini 応答から JSON オブジェクトを取り出してパースする。

    コードフェンス ```json ... ``` や前後テキストを許容する。
    """
    if not text:
        raise ValueError("empty response")
    candidate = text
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if m:
            candidate = m.group(1)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in response: {text[:200]}")
    return json.loads(candidate[start : end + 1])
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && venv/bin/python -m pytest test_ai_commentary.py -q`
Expected: PASS（4 件）

- [ ] **Step 5: コミット**

```bash
git add backend/ai_commentary.py backend/test_ai_commentary.py
git commit -m "$(printf 'feat: ai_commentary に extract_json を追加\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: ai_commentary.py — `_coerce` と `generate_commentary`（TDD）

**Files:**
- Modify: `backend/ai_commentary.py`
- Test: `backend/test_ai_commentary.py`

- [ ] **Step 1: 失敗するテストを追記**

`backend/test_ai_commentary.py` に追記:

```python
def test_coerce_clamps_confidence():
    assert ac._coerce({"confidence": 250, "summary": "x"})["confidence"] == 100
    assert ac._coerce({"confidence": -5, "summary": "x"})["confidence"] == 0


def test_coerce_requires_summary():
    assert ac._coerce({"confidence": 50, "summary": ""}) is None
    assert ac._coerce({"confidence": 50}) is None
    assert ac._coerce("not a dict") is None


def test_coerce_risks_normalized():
    out = ac._coerce({"confidence": 50, "summary": "x", "risks": ["a", "b", "c", "d", "e", "f"]})
    assert out["risks"] == ["a", "b", "c", "d", "e"]  # 最大5件
    out2 = ac._coerce({"confidence": 50, "summary": "x", "risks": "単一文字列"})
    assert out2["risks"] == ["単一文字列"]


def test_generate_commentary_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert ac.generate_commentary({"ticker": "8306.T"}, {}) is None


def test_generate_commentary_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(
        ac, "_generate_text",
        lambda prompt: '{"confidence": 72, "summary": "押し目買い。", "risks": ["決算が近い"]}',
    )
    out = ac.generate_commentary({"ticker": "8306.T", "direction": "buy"}, {})
    assert out == {"confidence": 72, "summary": "押し目買い。", "risks": ["決算が近い"]}


def test_generate_commentary_bad_json_then_none(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(ac, "_generate_text", lambda prompt: "JSONではない応答")
    assert ac.generate_commentary({"ticker": "8306.T"}, {}) is None


def test_generate_commentary_api_error_returns_none(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    def boom(prompt):
        raise RuntimeError("network")
    monkeypatch.setattr(ac, "_generate_text", boom)
    assert ac.generate_commentary({"ticker": "8306.T"}, {}) is None
```

- [ ] **Step 2: 失敗を確認**

Run: `cd backend && venv/bin/python -m pytest test_ai_commentary.py -q`
Expected: FAIL（`_coerce` / `generate_commentary` 未定義）

- [ ] **Step 3: 実装を追記**

`backend/ai_commentary.py` の末尾に追記:

```python
def build_prompt(plan: dict, market_ctx: dict) -> str:
    detail = plan.get("detail") or {}
    return "\n".join([
        "あなたは日本株スイングトレード（数日〜1週間）の作戦補佐です。",
        "以下のテクニカル判定データをもとに、なぜこの方向なのかの根拠・確信度・主なリスクを日本語で簡潔にまとめてください。",
        "予測の保証はできません。投資判断は利用者の自己責任です。",
        "新しい数値（指値・損切り等）は作らず、与えられた値の解釈に徹してください。",
        "",
        f"銘柄: {plan.get('ticker')} {plan.get('name') or ''}",
        f"判定: {plan.get('direction')}（スコア {plan.get('score')}）",
        f"終値: {plan.get('close')}",
        f"指標内訳: {json.dumps(detail, ensure_ascii=False)}",
        f"出来高倍率: {plan.get('vol_ratio')}",
        f"週足トレンド: {plan.get('weekly_trend')}",
        f"提案指値: {plan.get('limit_price')} / 利確: {plan.get('target_price')} / 損切: {plan.get('stop_price')}",
        f"地合い(指数トレンド): {market_ctx.get('index_trend')}",
        f"決算まで日数: {market_ctx.get('days_to_earnings')}",
        "",
        "次のJSON形式のみで出力してください（前後に文章を付けない）:",
        '{"confidence": <0-100の整数>, "summary": "<2〜3文の根拠>", "risks": ["<短いリスク>", "..."]}',
    ])


def _generate_text(prompt: str) -> str:
    """Gemini を呼んで生のテキストを返す。テストはここを monkeypatch する。

    import は実行時のみ（SDK 未導入でも本モジュールの読込・単体テストは通る）。
    """
    import google.generativeai as genai

    genai.configure(api_key=_api_key())
    model = genai.GenerativeModel(_model_name())
    resp = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json", "temperature": 0.4},
    )
    return resp.text or ""


def _coerce(obj: Any) -> dict | None:
    """Gemini の出力を {confidence:int(0-100), summary:str, risks:[str]} に整える。"""
    if not isinstance(obj, dict):
        return None
    try:
        conf = int(round(float(obj.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(100, conf))
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    risks = obj.get("risks") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [str(r) for r in risks if str(r).strip()][:5]
    return {"confidence": conf, "summary": summary.strip(), "risks": risks}


def generate_commentary(plan: dict, market_ctx: dict) -> dict | None:
    """作戦1行分のAI解説を生成。キー無し/API失敗は None（作戦ボードは継続）。"""
    if not _api_key():
        return None
    prompt = build_prompt(plan, market_ctx)
    for _ in range(2):  # JSONパース失敗時に1回だけリトライ
        try:
            text = _generate_text(prompt)
        except Exception:
            return None  # API失敗 → スキップ（v1は待機/バックオフなし）
        try:
            obj = extract_json(text)
        except Exception:
            continue  # パース失敗 → リトライ
        return _coerce(obj)
    return None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && venv/bin/python -m pytest test_ai_commentary.py -q`
Expected: PASS（全件）

- [ ] **Step 5: コミット**

```bash
git add backend/ai_commentary.py backend/test_ai_commentary.py
git commit -m "$(printf 'feat: generate_commentary（Gemini解説の生成・検証・リトライ）\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: market.fetch_earnings_days（best-effort）

**Files:**
- Modify: `backend/market.py`

- [ ] **Step 1: 関数を追加**

`backend/market.py` の末尾（`fetch_name` の後）に追加:

```python
def fetch_earnings_days(ticker: str) -> int | None:
    """直近の将来決算日までの日数（best-effort）。取得不可・無しは None。

    yfinance の決算日は日本株では欠落しがち。例外は握りつぶして None を返す。
    """
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).get_earnings_dates(limit=12)
        if df is None or df.empty:
            return None
        idx = pd.to_datetime(df.index)
        now = pd.Timestamp.now(tz=idx.tz)
        future = idx[idx >= now]
        if len(future) == 0:
            return None
        return int((future.min().normalize() - now.normalize()).days)
    except Exception:
        return None
```

- [ ] **Step 2: スモーク確認（インポートが壊れていないこと）**

Run: `cd backend && venv/bin/python -c "import market; print(hasattr(market, 'fetch_earnings_days'))"`
Expected: `True`

（ネットワーク前提のため値の単体テストはしない。例外時 None の graceful 設計）

- [ ] **Step 3: コミット**

```bash
git add backend/market.py
git commit -m "$(printf 'feat: market.fetch_earnings_days（決算までの日数・best-effort）\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: db.py — daily_plan の3列追加・マイグレーション・upsert_plan 拡張

**Files:**
- Modify: `backend/db.py`

- [ ] **Step 1: SCHEMA の daily_plan に3列追加**

`backend/db.py` の `daily_plan` CREATE 文（`rationale TEXT,` の行）を次に置換:

```sql
  rationale     TEXT,
  ai_summary    TEXT,
  ai_confidence INTEGER,
  ai_risks      TEXT,
```

（`created_at ... UNIQUE (ticker, plan_date)` はそのまま）

- [ ] **Step 2: マイグレーション関数を追加し init_db で呼ぶ**

`backend/db.py` の `init_db()` 内、`conn.executescript(SCHEMA)` の直後に追加:

```python
        _migrate_daily_plan(conn)
```

そして `init_db` 定義の直前に関数を追加:

```python
def _migrate_daily_plan(conn):
    """既存 data.db の daily_plan に AI解説の列が無ければ追加（冪等）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(daily_plan)").fetchall()}
    for col, decl in (("ai_summary", "TEXT"), ("ai_confidence", "INTEGER"), ("ai_risks", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE daily_plan ADD COLUMN {col} {decl}")
```

- [ ] **Step 3: upsert_plan を3列対応に**

`backend/db.py` の `upsert_plan` を次に置換:

```python
def upsert_plan(row: dict):
    """1銘柄分の作戦を (ticker, plan_date) で upsert。"""
    row = {**row}
    for k in ("ai_summary", "ai_confidence", "ai_risks"):
        row.setdefault(k, None)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_plan "
            "(ticker, plan_date, direction, score, vol_ratio, weekly_trend, "
            " limit_price, stop_price, target_price, rationale, "
            " ai_summary, ai_confidence, ai_risks) "
            "VALUES (:ticker, :plan_date, :direction, :score, :vol_ratio, :weekly_trend, "
            " :limit_price, :stop_price, :target_price, :rationale, "
            " :ai_summary, :ai_confidence, :ai_risks) "
            "ON CONFLICT(ticker, plan_date) DO UPDATE SET "
            "direction=excluded.direction, score=excluded.score, vol_ratio=excluded.vol_ratio, "
            "weekly_trend=excluded.weekly_trend, limit_price=excluded.limit_price, "
            "stop_price=excluded.stop_price, target_price=excluded.target_price, "
            "rationale=excluded.rationale, ai_summary=excluded.ai_summary, "
            "ai_confidence=excluded.ai_confidence, ai_risks=excluded.ai_risks, "
            "created_at=datetime('now')",
            row)
```

- [ ] **Step 4: マイグレーションが既存DBで通ることを確認**

Run: `cd backend && venv/bin/python -c "import db; db.init_db(); import sqlite3; print([r[1] for r in sqlite3.connect(db.DB_PATH).execute('PRAGMA table_info(daily_plan)')])"`
Expected: 列名に `ai_summary`, `ai_confidence`, `ai_risks` が含まれる

- [ ] **Step 5: コミット**

```bash
git add backend/db.py
git commit -m "$(printf 'feat: daily_plan に AI解説の3列を追加（新規/既存DB対応）\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: main.py — perform_refresh に 地合い・決算・AI解説 を統合

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: import を追加**

`backend/main.py` の import 群を更新:
- 先頭付近に `import json` と `import os` を追加（未 import の場合）。
- `from market import fetch_name, get_history` を `from market import fetch_earnings_days, fetch_name, get_history` に。
- `from signals import build_plan, evaluate, resolve_configs` を `from signals import build_plan, evaluate, resolve_configs, weekly_trend` に。
- 新規 `from ai_commentary import generate_commentary` を追加。

- [ ] **Step 2: 地合い（指数トレンド）を1回だけ取得**

`perform_refresh` の `results = []` / `failed = []` の直前に追加:

```python
    # 地合い（指数トレンド）を1回だけ取得し全銘柄で使い回す（best-effort）
    index_trend = None
    try:
        index_ticker = os.environ.get("GEMINI_INDEX_TICKER", "^N225")
        idx_df = get_history(index_ticker, period=period, demo=demo)
        if not idx_df.empty:
            index_trend = weekly_trend(idx_df)
    except Exception:
        index_trend = None
```

- [ ] **Step 3: AI解説を生成して upsert_plan に渡す**

`perform_refresh` の `plan = build_plan(...)` と `db.upsert_plan({...})` の間に解説生成を追加し、`upsert_plan` の dict に3キーを足す:

```python
        # 作戦ボード（強化4）: 翌営業日の提案指値・出口を生成して保存
        plan = build_plan(df, direction, score, ticker_cfgs)
        plan_date = _next_business_day(date)

        # AI解説（Gemini・無料枠・best-effort）。キー無し/失敗は None で従来どおり。
        days_to_earnings = None if demo else fetch_earnings_days(ticker)
        commentary = generate_commentary(
            {"ticker": ticker, "name": w.get("name"), "direction": direction,
             "score": score, "detail": detail, "vol_ratio": detail.get("vol_ratio"),
             "weekly_trend": detail.get("weekly_trend"), "close": last_close,
             "limit_price": plan["limit_price"], "stop_price": plan["stop_price"],
             "target_price": plan["target_price"]},
            {"index_trend": index_trend, "days_to_earnings": days_to_earnings},
        )

        db.upsert_plan({
            "ticker": ticker, "plan_date": plan_date, "direction": direction, "score": score,
            "vol_ratio": detail.get("vol_ratio"), "weekly_trend": detail.get("weekly_trend"),
            "limit_price": plan["limit_price"], "stop_price": plan["stop_price"],
            "target_price": plan["target_price"], "rationale": plan["rationale"],
            "ai_summary": commentary["summary"] if commentary else None,
            "ai_confidence": commentary["confidence"] if commentary else None,
            "ai_risks": json.dumps(commentary["risks"], ensure_ascii=False) if commentary else None,
        })
```

（既存の `db.upsert_plan({...})` 呼び出しはこの新ブロックに置換する。重複させないこと）

- [ ] **Step 4: import とリフレッシュ経路が壊れていないことを確認**

Run: `cd backend && venv/bin/python -c "import main; print('ok')"`
Expected: `ok`（import エラーなし）

- [ ] **Step 5: コミット**

```bash
git add backend/main.py
git commit -m "$(printf 'feat: perform_refresh に地合い・決算・AI解説を統合\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: backend 結合テスト（キー無しで従来どおり）

**Files:**
- 確認のみ（必要なら `backend/test_api.py` に1ケース追加）

- [ ] **Step 1: 既存テストを全実行**

Run: `cd backend && venv/bin/python -m pytest -q`
Expected: 既存 + 新規（ai_commentary）すべて PASS。`GEMINI_API_KEY` 未設定環境では `/refresh`→`/plan` は AI 列が空でも成功すること。

- [ ] **Step 2: （任意）AI列が空でも plan 取得が成功するテストを追加**

`backend/test_api.py` に、`/refresh?demo=true` 後の `/plan` 行に `ai_summary` キーが存在し（値は None 可）、エラーにならないことを確認するケースがなければ追加。

- [ ] **Step 3: コミット（テスト追加時のみ）**

```bash
git add backend/test_api.py
git commit -m "$(printf 'test: キー未設定でも refresh→plan が成功することを確認\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 8: frontend — PlanRow 型に3フィールド追加

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: PlanRow を更新**

`frontend/src/lib/api.ts` の `PlanRow` 型、`rationale: string | null;` の直後に追加:

```typescript
  rationale: string | null;
  ai_summary: string | null;
  ai_confidence: number | null;
  ai_risks: string | null; // JSON文字列化された string[]
  created_at: string;
```

- [ ] **Step 2: 型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし

- [ ] **Step 3: コミット**

```bash
git add frontend/src/lib/api.ts
git commit -m "$(printf 'feat(ui): PlanRow 型に AI解説フィールドを追加\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 9: frontend — 作戦カードに「AI解説」表示

**Files:**
- Modify: `frontend/src/app/plan/page.tsx`

- [ ] **Step 1: AiCommentary コンポーネントを追加**

`frontend/src/app/plan/page.tsx` の末尾（`PlanMetric` 関数の後）に追加:

```tsx
function AiCommentary({
  summary, confidence, risksJson,
}: { summary: string; confidence: number | null; risksJson: string | null }) {
  let risks: string[] = [];
  if (risksJson) {
    try {
      const p = JSON.parse(risksJson);
      if (Array.isArray(p)) risks = p.map(String);
    } catch {
      /* 壊れたJSONは無視 */
    }
  }
  return (
    <div className="mt-3 rounded border border-indigo-100 bg-indigo-50 p-3">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-xs font-semibold text-indigo-700">AI解説</span>
        {confidence != null && (
          <span className="rounded bg-indigo-600 px-1.5 py-0.5 text-xs font-semibold text-white">
            確信度 {confidence}
          </span>
        )}
      </div>
      <p className="text-sm text-slate-700">{summary}</p>
      {risks.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
          {risks.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-[10px] text-slate-400">
        AIによる説明であり予測の保証ではありません。
      </p>
    </div>
  );
}
```

- [ ] **Step 2: PlanCard 内で描画**

`PlanCard` の return 内、最後の分岐ブロック（actionable/holding/neutral の三項）の直後・カード `</div>` の直前に追加:

```tsx
      {row?.ai_summary && (
        <AiCommentary
          summary={row.ai_summary}
          confidence={row.ai_confidence}
          risksJson={row.ai_risks}
        />
      )}
```

- [ ] **Step 3: 型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラーなし

- [ ] **Step 4: コミット**

```bash
git add frontend/src/app/plan/page.tsx
git commit -m "$(printf 'feat(ui): 作戦カードに AI解説（確信度・根拠・リスク）を表示\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 10: frontend テスト/ビルド確認

**Files:**
- 確認のみ

- [ ] **Step 1: 既存 Vitest を実行**

Run: `cd frontend && npm test`
Expected: 既存テストすべて PASS（AI解説の追加で壊れていないこと）

- [ ] **Step 2: 本番ビルド（型/構文の最終確認）**

Run: `cd frontend && npm run build`
Expected: 成功

---

## Task 11: キー設定と動作確認・README追記

**Files:**
- Create: `backend/.env`（gitignore済・コミットしない）
- Modify: `README.md`（任意・AI解説の使い方を1段落）

- [ ] **Step 1: backend/.env を作成（キーは listing-text-generator から流用）**

```bash
LTG=/Users/toru/code/listing-text-generator/.env.local
KEY=$(grep -E '^GEMINI_API_KEY=' "$LTG" | head -1 | cut -d= -f2-)
MODEL=$(grep -E '^GEMINI_MODEL=' "$LTG" | head -1 | cut -d= -f2-)
printf 'GEMINI_API_KEY=%s\nGEMINI_MODEL=%s\nGEMINI_INDEX_TICKER=^N225\n' "$KEY" "${MODEL:-gemini-2.5-flash}" > backend/.env
echo "wrote backend/.env (keys hidden)"; sed -E 's/=.*/=<hidden>/' backend/.env
```

- [ ] **Step 2: .env が gitignore されていることを確認（コミットされない）**

Run: `git check-ignore backend/.env`
Expected: `backend/.env`（=無視対象）

- [ ] **Step 3: SDK を venv に導入（実際のGemini呼び出し用。ネットワーク要）**

Run: `cd backend && venv/bin/pip install google-generativeai==0.8.6 python-dotenv==1.1.1`
Expected: 成功（PyPI 接続不可なら後でユーザーが実行。単体テストは未導入でも通る）

- [ ] **Step 4: 実動作の best-effort 確認（ネットワーク要・任意）**

API を起動して demo で生成 → /plan に AI解説が載るか確認:

```bash
cd backend && venv/bin/uvicorn main:app --port 8000 &  # 別ターミナルでも可
sleep 3
curl -s -X POST "http://localhost:8000/plan/generate?demo=true" >/dev/null
curl -s "http://localhost:8000/plan" | venv/bin/python -c "import sys,json; rows=json.load(sys.stdin)['rows']; print('ai_summary 付き:', sum(1 for r in rows if r.get('ai_summary')), '/', len(rows))"
```
Expected: `ai_summary 付き: N / M`（N>0 ならGemini連携OK）。ネットワーク制限環境では取得失敗で 0 になり得る（その場合はローカルで再確認）。

- [ ] **Step 5: README に1段落追記（任意）→ コミット**

`README.md` に「作戦ボードのAI解説（Gemini・無料枠）」を1段落（キーは `backend/.env` の `GEMINI_API_KEY`、未設定でも従来動作、`google-generativeai` の導入要）。

```bash
git add README.md
git commit -m "$(printf 'docs: 作戦ボードのAI解説（Gemini）の使い方を追記\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## 完了の定義（Acceptance）

1. `GEMINI_API_KEY` 設定＋`google-generativeai` 導入時、`/plan/generate` 後に `/plan` の各カードへ確信度・要約・リスクが表示される。
2. キー未設定（または SDK 未導入）でも `/refresh`→`/plan` が従来どおり成功し、AI解説欄は非表示。
3. 一部銘柄で Gemini 失敗でも全体処理は完了。
4. キー（`backend/.env`）はコミットされない。
5. 新規・既存どちらの `data.db` でも列マイグレーションが通る。
6. backend `pytest` と frontend `npm test` / `npm run build` が緑。
