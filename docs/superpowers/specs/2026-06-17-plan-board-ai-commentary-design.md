# 設計書：作戦ボードの「AI解説」（Gemini・無料枠）

- 日付: 2026-06-17
- 対象アプリ: invest-analyzer（日足スイング向け 株価シグナル/作戦立案アプリ）
- 関連: `課題.md`（課題2 地合い・課題7 決算回避・課題10 説明力）、`課題.md §3.5`（Gemini無料枠で実質¥0）

---

## 1. 目的（Goal）

作戦ボード（`/plan`）の各銘柄カードに、Gemini が生成する日本語の「AI作戦解説」を付ける：

1. **なぜ買い / 売り / 中立か**（既存スコア・指標内訳・出口・地合い・決算日を根拠に）
2. **確信度（0〜100 の整数）**
3. **主なリスク**（決算が近い・週足逆行・出来高薄い 等／文字列のリスト）

これにより、現状「数値の羅列」だった `rationale` を人が読んで納得できる作戦コメントに引き上げる（課題10）。同時に、地合い（指数トレンド・課題2）と決算日（課題7）を無料データで作戦に織り込む第一歩とする。

**このアプリの方針は維持**：自動売買はしない／投資は自己責任の免責を残す。AI解説は予測の保証ではない。

---

## 2. スコープ

### 含む（In scope）
- backend に Gemini 呼び出しモジュールを追加し、`/refresh`（作戦生成）時に各銘柄の AI 解説を生成して `daily_plan` に保存。
- 入力に「地合い（指数トレンド）」と「決算日フラグ」を無料データで best-effort 追加。
- frontend `/plan` の各カードに AI 解説（確信度バッジ＋要約＋リスク）を表示。
- Gemini 未設定・失敗時でも作戦ボードが従来どおり動作する graceful degradation。

### 含まない（Out of scope・次段）
- ニュース／適時開示の取得・要約（Tier 2 本丸。日本株の無料ニュースが不安定なため後回し）。
- 確信度に基づく Top N ランキング／クロスセクション相対力（課題3）。今回は確信度を「出す」ところまで。
- スコアリング設計の刷新・出口/サイジングの数式改善（課題1,4,5）。
- **保有(holding)コンテキストを AI 解説に渡すこと**（`perform_refresh` に holdings 読み込みを足す net-new 配線が要るため次段）。
- 認証・クラウド対応。

---

## 3. 前提（現状の関連実装）

- backend は Python / FastAPI。`backend/signals.py`（`evaluate` / `build_plan` / `weekly_trend`）、`backend/market.py`（`get_history`）、`backend/db.py`（SQLite）、`backend/main.py`（`perform_refresh`）。
- `daily_plan` テーブル（`db.py`）:
  ```sql
  CREATE TABLE IF NOT EXISTS daily_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, plan_date TEXT NOT NULL,
    direction TEXT, score INTEGER, vol_ratio REAL, weekly_trend TEXT,
    limit_price REAL, stop_price REAL, target_price REAL, rationale TEXT,
    created_at TEXT DEFAULT (datetime('now')), UNIQUE (ticker, plan_date)
  );
  ```
- `perform_refresh()`（`main.py`）が銘柄ごとに `evaluate` → `build_plan` → `db.upsert_plan({...})` を実行。
- `list_plan()` は `SELECT *` で返すため、列を追加すれば API/フロントに自動で流れる。
- 既存 DB（`data.db`）があるため、列追加は `ALTER TABLE ADD COLUMN` のマイグレーションが必要（`CREATE TABLE IF NOT EXISTS` は既存テーブルを変更しない）。
- 参照プロジェクト `../listing-text-generator`（Next.js/TS）が Gemini を使用：環境変数 `GEMINI_API_KEY`、`GEMINI_MODEL`（既定 `gemini-2.5-flash`）、SDK `@google/generative-ai`、応答から JSON を抽出する `extractJson`。本アプリは Python なので **同じ環境変数・同じモデル**を Python SDK `google-generativeai` で用いる。

---

## 4. アーキテクチャ

### 4.1 新規モジュール `backend/ai_commentary.py`

責務：作戦1行分のデータと市場コンテキストを受け取り、Gemini で AI 解説を生成して構造化データで返す。Gemini 以外の知識を持たない単機能。

```
generate_commentary(plan: dict, market_ctx: dict) -> dict | None
  入力 plan:        {ticker, name, direction, score, detail(指標内訳),
                     vol_ratio, weekly_trend, close,
                     limit_price, stop_price, target_price}
  入力 market_ctx:  {index_trend: 'up'|'down'|'flat'|None,
                     days_to_earnings: int|None}
  戻り値:           {confidence: int(0-100), summary: str, risks: list[str]}
                     生成不可（キー無し/失敗）の場合は None
```

- `name`（銘柄名）は `perform_refresh` のウォッチリスト行 `w["name"]` から渡す（`evaluate`/`build_plan` の出力ではない）。
- **v1では保有(holding)コンテキストは入力に含めない**（`perform_refresh` は現状 holdings を読まないため、net-new の配線になる。スコープ最小化のため次段に回す → §2 参照）。

- 設定読み込み：`GEMINI_API_KEY`（必須・無ければ即 `None`）、`GEMINI_MODEL`（既定 `gemini-2.5-flash`）。
- プロンプト：システム的な指示（あなたはスイングトレードの作戦補佐。与えられた指標から日本語で簡潔に根拠・確信度・リスクを述べる。予測の保証はしない）＋ plan/market_ctx を整形して渡す。**出力は JSON のみ**を要求。
- JSON 抽出：`extract_json(text)`（コードフェンス/前後テキストを許容、`{` 〜 `}` を取り出して `json.loads`）。listing-text-generator の `extractJson` の Python 版。
- バリデーション：`confidence` を 0–100 にクランプ、`summary` は文字列、`risks` はリスト（最大 5 件程度に丸め）。型不正は補正 or `None`。

### 4.2 市場コンテキスト取得（無料・best-effort）

- **地合い（index_trend）**：`market.get_history(index, demo=demo)`（既定 `^N225`・設定可）の日足に既存 `signals.weekly_trend()` を適用して `up/down/flat` を得る。`perform_refresh` で **1 回だけ**取得して全銘柄で使い回す（呼び出し削減）。取得失敗時は `None`。demo モードでは合成データのため index_trend は実指数を表さない（best-effort・`demo` を踏襲してオフライン動作を保つ）。
- **決算日（days_to_earnings）**：`market.fetch_earnings_days(ticker)` を新設。yfinance `Ticker(ticker).get_earnings_dates()` 等から直近の将来決算日までの営業日数を best-effort 算出。例外/空は `None`（JP銘柄では取れないことがある前提）。

### 4.3 生成タイミングと保存（案A：保存型）

- `perform_refresh()` 内、`build_plan` 後・`upsert_plan` 前に `generate_commentary` を呼ぶ。
- 結果を `daily_plan` の新列に保存：`ai_summary TEXT`, `ai_confidence INTEGER`, `ai_risks TEXT`(JSON文字列)。
- `upsert_plan` の INSERT/UPDATE に 3 列を追加。
- **無料枠保護**：呼び出しは「リフレッシュ時×監視銘柄数」のみ（数十/日 ≪ 1,500/日）。**v1 は失敗スキップのみ**（429/例外/タイムアウトはその銘柄を AI 解説なしで継続。待機/バックオフのループは入れない。リトライは §6 の JSON パース 1 回のみ）。RPM 対策の pacing は将来の課題。

### 4.4 DB マイグレーション

- `db.py` の初期化時に `daily_plan` へ列が無ければ `ALTER TABLE daily_plan ADD COLUMN ...` を実行（`PRAGMA table_info` で存在確認して冪等化）。新規 DB は `CREATE TABLE` に最初から3列を含める。

### 4.5 API

- エンドポイント定義は変更なし。`GET /plan` は `{plan_date, rows:[...]}` を返し、`list_plan` が `SELECT *` のため新列は rows に自動的に含まれる。
- ただしフロントは `frontend/src/lib/api.ts` の型 `PlanRow`（フィールドを列挙）経由で消費するため、**`PlanRow` 型に `ai_summary`/`ai_confidence`/`ai_risks` を追加する**必要がある（DB の `SELECT *` だけでは型付きクライアントに届かない）。

### 4.6 frontend `/plan`

- 既存の作戦カードに「AI解説」セクションを追加：
  - 確信度バッジ（例：`確信度 72`）。
  - 要約（`ai_summary`）。
  - リスク（`ai_risks` を `JSON.parse` して箇条書き）。
- `ai_summary` が無いカードはセクション非表示（従来表示のまま）。
- 既存の免責表示は維持。

---

## 5. データフロー

```
[/refresh] perform_refresh()
  ├─ 地合い: get_history("^N225") → weekly_trend() → index_trend  (1回)
  └─ 各 enabled 銘柄:
       get_history(ticker) → evaluate() → build_plan()
       fetch_earnings_days(ticker) → days_to_earnings (best-effort)
       generate_commentary(plan, {index_trend, days_to_earnings})
         → {confidence, summary, risks} | None
       db.upsert_plan({...既存..., ai_summary, ai_confidence, ai_risks})
[/plan] GET → list_plan() (SELECT *) → frontend がカードに AI解説を描画
```

---

## 6. エラー処理・フォールバック

| 事象 | 挙動 |
|---|---|
| `GEMINI_API_KEY` 未設定 | `generate_commentary` が即 `None`。作戦ボードは従来どおり動作（完全オプトイン） |
| Gemini API 失敗 / タイムアウト / 429 | その銘柄は AI 解説なしで保存し継続（全体は止めない）。ログに記録 |
| JSON パース失敗 | 1 回リトライ → だめなら `None` |
| 決算日/地合い取得失敗 | 当該コンテキストを `None` にして続行（解説は他の根拠で生成） |
| demo モード | キーがあれば合成データの数値を説明（動作確認可） |

---

## 7. 設定・キーの扱い

- `backend/.env`（gitignore 済みにする）に以下を置く：
  ```
  GEMINI_API_KEY=<listing-text-generator/.env.local と同じ値>
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_INDEX_TICKER=^N225   # 任意・地合い判定の指数
  ```
- backend は起動時に `.env` を読む（`python-dotenv` を requirements に追加、もしくは uvicorn 起動時に環境変数で渡す）。
- `requirements.txt` に `google-generativeai`（と必要なら `python-dotenv`）を追加。
- `.gitignore` に `backend/.env` を確認・追加（キーをコミットしない）。

---

## 8. テスト

- `backend/test_ai_commentary.py`（新規）
  - `extract_json`：コードフェンス有/無・前後テキスト有のパース。
  - `generate_commentary`：Gemini 呼び出しをモックし、(a) 正常 JSON → 構造化結果、(b) 不正 JSON → リトライ後 `None`、(c) キー未設定 → `None`、(d) confidence のクランプ。
- 既存 `backend/test_api.py`：`/refresh`→`/plan` がキー未設定でも従来どおり成功すること（AI列は空でよい）を確認/追加。
- frontend：`/plan` の行整形に AI 解説フィールドが含まれても既存テストが壊れないこと（必要なら最小テスト追加）。

---

## 9. リスク・留意点

- **Gemini 無料枠の RPM 上限**：監視銘柄が多いと連続呼び出しで 429 になり得る。→ 失敗スキップ＋必要なら軽い待機で対応（解説は best-effort）。
- **無料枠のデータ利用**：無料枠の入力は Google のサービス改善に使われ得る。扱うのは市場の公開情報＋自前の指標なので実害は小さいが、設計書に明記。
- **決算日の取得精度**：yfinance の決算日は JP 銘柄で欠落しがち。`None` 前提で設計（取れたら活用）。
- **品質のばらつき**：LLM 生成文は誤りうる。免責表示を維持し、数値（指値・出口）は従来どおりルールベースの値を正とする（AI は説明役で、売買数値の決定はしない）。

---

## 10. 受け入れ基準（Acceptance）

1. `GEMINI_API_KEY` を設定して `/refresh` を実行すると、`/plan` の各カードに確信度・要約・リスクが表示される。
2. キー未設定でも `/refresh`→`/plan` が従来どおり成功し、AI 解説欄は非表示。
3. 一部銘柄で Gemini が失敗しても、他銘柄の作戦と全体処理は完了する。
4. キーがコミットされていない（`backend/.env` は gitignore）。
5. 新規・既存どちらの `data.db` でも列マイグレーションが通り、エラーにならない。
