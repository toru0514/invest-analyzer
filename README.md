# 株価シグナル通知アプリ

複数の日本株を監視し、複数のテクニカル指標を組み合わせたスコアで
「買い時 / 売り時」を判定・通知する、**ローカル完結のスイングトレード支援アプリ**。

- 対象は**スイング（数日〜数週間）**。日足ベースで判定する。
- **自動売買はしない。** シグナルを通知するところまでが責務（売買は人間が行う）。
- デイトレ（板読み・秒単位）は対象外。データ源（yfinance）が遅延データのため。

> ⚠️ **免責**: テクニカル指標は予測を保証しません。本ツールは投資助言ではありません。
> 売買の判断と結果はすべて自己責任です。

---

## 開発状況

| Phase | 内容 | 状況 |
|---|---|---|
| **Phase 0** | Python 計算コアの検証（yfinance + pandas-ta + バックテスト、UIなし） | ✅ 実装済み |
| **Phase 1** | FastAPI + SQLite でAPI化・データ保存 | ✅ 実装済み |
| **Phase 2** | ブラウザ通知 / 指定金額アラート（price_target） | ✅ 実装済み |
| **Phase 3** | Next.js UI（ダッシュボード・チャート・設定・シミュレーション） | ✅ 実装済み |
| **Phase 4** | 状態ベース・スコア再設計／閾値・params の UI 編集／指定金額アラート UI／日次自動更新／テスト整備 | ✅ 実装済み（運用・チューニングは継続） |

---

## 進捗まとめ（やったこと / まだなこと / これからやること）

> **2026-06 更新（Phase 8.1 = 保有銘柄の出口表示）**:
>
> - **保有している銘柄は、判定が中立でも ATR ベースの利確/損切（出口の目安）を表示**。
>   `build_plan` を拡張し neutral でも `stop_price`/`target_price`（long 側・close ∓ mult×ATR）を算出（提案指値は出さない）。
>   保有株での損益（円換算）も併記。これで「保有中だが中立」の銘柄でも損切/利確の目安が分かる。
>
> **2026-06 更新（Phase 8 = 作戦ボードの全銘柄表示）**:
>
> - **作戦ボードに監視中の全銘柄をカード表示**（中立も含む。各カードで保有登録・現在値・含み損益が見える）。
>   並び順は 買い/売り → 保有 → 中立 → 作戦未生成。銘柄コードに加えて銘柄名も表示。
> - **作戦ボードから監視銘柄を追加**（ティッカー＋銘柄名）。追加後「作戦を生成」で判定・価格を取得。
>
> **2026-06 更新（Phase 7 = 保有ポジション）**:
>
> - **作戦ボードで保有ポジションを直接登録**（取得単価・株数）。`holdings` テーブル＋ `GET/PUT /holdings`・`DELETE /holdings/{ticker}`。
> - 保有銘柄は **現在値・評価額・含み損益（円/％）** を表示。利確/利確ライン到達時の **保有株での損益（円換算）** も併記。
>   保有合計の含み損益をヘッダに表示。保有銘柄は中立でもカード表示。
>
> **2026-06 更新（Phase 6.1 = スイング向け指値）**:
>
> - **買いの提案指値の既定を「5日線の近辺」に変更**（旧: 20日安値×1.003＝深すぎてトレンド中は不約定）。
>   `limit_method` 既定を `support`→`ma`、移動平均の期間（`limit_ma`）と ATR 押し目の深さ（`entry_atr_mult`）を設定で調整可能。
>   買いは現値を超えない・売りは現値を下回らないようキャップ（押し目買い/戻り売り）。
>
> **2026-06 更新（Phase 6 = 銘柄別の出口設定）**:
>
> - **ATR 出口を銘柄別に上書き可能に**: 設定の「銘柄別の出口設定（ATR・任意）」で、銘柄ごとに
>   利確×・損切×・指値方式を個別設定（未設定の銘柄は全銘柄共通を使用）。「銘柄によって効く出口が違う」に対応。
>   作戦ボードの提案指値・利確/損切に反映（`resolve_configs` が `rule_type` 単位で銘柄固有→共通の順に解決）。
> - **スイング向けに既定の利確×を 2.0 → 1.5 に変更**（数日〜1週間の回転を想定。検証では勝率↑・平均保有↓）。
>
> **2026-06 更新（Phase 5 = README「これからやること」）**:
>
> - **チューニング自動化**: 新画面 `/optimize`（`POST /optimize`）。スコア閾値スイープ（±2/±3/±4 × score/atr）と、
>   各指標を1つずつ外す leave-one-out の寄与度をバックテストで算出してランキング。「この閾値を適用」で設定に保存。
> - **指標を拡充**: **OBV**（出来高トレンド）と **CCI**（売られすぎ/買われすぎ）を追加（合計 9 スコア指標）。
> - **frontend テスト整備**: Vitest + Testing Library を導入（`cd frontend && npm test`）。
>   行整形ロジック・API クライアント・コンポーネントの単体テスト。
> - **スケジューラ堅牢化**: 日本の市場休業日（祝日）カレンダーを内蔵し、`scheduler_skip_holidays`（既定 ON）で休業日をスキップ。
>   launchd / cron による常駐手順を「依存・環境について」に記載。
>
> **2026-06 更新（追補版 = `今後の進め方.md`）**: 同じ OHLCV の範囲内で判断の質を上げる
> 4 強化を実装。
>
> - **出来高フィルター**（強化1）: `vol_ratio = volume / SMA(20)`。急増(≥1.5)でスコア +1、閑散(<0.7)で減衰。
> - **週足トレンド足切り**（強化2）: 日足を週足にリサンプルし SMA(13) の傾きで up/down/flat 判定。
>   逆行する向きのシグナルを block / penalty（既定 penalty）。マルチタイムフレームで逆張り事故を低減。
> - **ATR 出口設計**（強化3）: `ATR(14)` から損切（`close∓1.5·ATR`）・利確（`close±2.0·ATR`）を自動算出。
> - **作戦ボード**（強化4）: 翌営業日の判定・**提案指値**（サポート/MA/ATR）・利確/損切を一覧表示する
>   新画面 `/plan`。`refresh` 時に `daily_plan` へ一括生成。発注・自動売買はしない（人間が指値を置くための数字）。
> - **出口入りシミュレーション**（強化J）: シミュレーション画面の「ATR出口ルール」を ON にすると、
>   ハーフ ATR の押し目で約定し、損切/利確ラインまたは逆シグナルで決済。利確/損切/逆シグナル決済回数・
>   平均保有日数・リスクリワード実績を表示（出口設計の良し悪しを検証できる）。
> - **乖離率（disparity）指標を追加**（移動平均からの乖離%。売られすぎ→買い/買われすぎ→売り）。
> - ダッシュボードに「出来高倍率」「週足」列を追加。設定 UI で上記フィルター・指標の params も編集可能。
>
> **2026-06 更新（Phase 4）**: シグナルが実質出ない核心課題を解消し、運用に必要な
> UI/自動化/テストを実装。実データ（yfinance）でも買い/売り判定が定常的に出ることを確認済み。
>
> - **シグナルを状態ベースに再設計**: 旧版は「クロスした当日だけ」発火するエッジ型で
>   閾値 ±3 にほぼ到達せず、実データ 320 サンプルで buy=0 / sell=1 回しか出なかった。
>   トレンド系（MA の並び・MACD の符号）を常時 ±weight で評価し、逆張り系（RSI/ストキャス/BB）の
>   売られすぎ/買われすぎを加点する連続スコアに変更。既定閾値を **±2** にし、
>   実データのバックテストで実際に売買が成立（例: 直近約3ヶ月で 12 取引・勝率 60%）。
> - **スコア閾値・指標 params を設定 UI から編集可能に**（DB の `app_meta` / `signal_config` に保存）。
> - **指定金額アラート（price_target）の管理 UI**（銘柄別の上限/下限を画面から追加・削除）。
> - **日次自動更新スケジューラ**（API 常駐中に毎営業日の指定時刻で refresh→判定→通知。自動売買なし）。
> - **テスト整備**: API 結合テスト（FastAPI TestClient）＋スケジューラのロジックテストを追加（`make test`）。
> - **不具合修正**: CORS を localhost 任意ポート許可に／ダッシュボードの「現在値」が常に空だった件を修正。

### ✅ やったこと（実装済み）

- **計算コア（Phase 0）**
  - yfinance で日本株の日足を取得（`8306.T` 等）。`market.py`
  - pandas-ta で指標を一括計算：RSI / MACD / 移動平均クロス(GC/DC) /
    ボリンジャーバンド / ストキャスティクス / ローソク足パターン（赤三兵・三羽烏・包み足、TA-Lib 使用）
  - 重み付きスコアリングで `buy` / `sell` / `neutral` 判定。`signals.py` の `evaluate()`
  - 端株（小数株）対応の1ヶ月ペーパートレードで §4 成績を算出。`backtest.py`
  - look-ahead bias 回避（各営業日の判定はその日までのデータのみ）
  - CLI 検証スクリプト `phase0_backtest.py` とスモークテスト `test_signals.py`
- **API + DB（Phase 1）**
  - SQLite に §2 の全テーブル作成・初期データ投入。`db.py`（WAL モード）
  - FastAPI で §5 の全エンドポイントを実装。`main.py`
  - 取得した価格・判定結果を DB に保存（`price_data` / `signals`）
- **通知（Phase 2）**
  - シグナル発生時のブラウザ通知（`NotificationWatcher` が `/signals/unnotified` をポーリング）
  - 指定金額アラート `price_target`（スコアと独立した即時通知経路）
- **UI（Phase 3）** — Next.js (App Router / TypeScript / Tailwind)
  - ダッシュボード（TanStack Table・色分け判定・「データ更新＋再判定」）
  - 銘柄詳細（lightweight-charts のローソク足 ＋ シグナル履歴）
  - 設定（銘柄の追加/削除、各指標の重み・ON/OFF）
  - シミュレーション（バックテスト実行 ＋ recharts の資産推移グラフ ＋ §4 成績表）
  - 全画面に免責表示（投資は自己責任 / シグナルは予測を保証しない）
- **開発体験**: `make dev` で API と Next.js を一括起動。`make setup` / `phase0` / `test`

### 🚧 まだなこと（未実装・既知の制約）

- **戦略チューニング（重み・閾値・params の最適化）は継続課題**。仕組み・UI は完成したので、
  あとは実データのバックテストを見ながら「どの技が効くか」を詰める運用フェーズ。
- **出口入りシミュレーションのエントリーは簡易モデル**（ハーフ ATR の押し目指値・銘柄ごとに資金等分・
  同時1ポジション・約定はハーフATRの押し目）。提案指値方式そのままでの約定や、資金共有の最適配分は今後の課題。
- **週足は日足からリサンプルして算出**（追補版の「週足を別 fetch・`timeframe` 列で保持」ではなく、
  同一日足データから週足化）。look-ahead を避けやすく追加の取得も不要なため。挙動（週足 SMA13 の傾き）は同じ。
- **現状の指標は 9 種**（RSI/MA/MACD/BB/ストキャス/ローソク足/乖離率/OBV/CCI）＋
  出来高/週足/ATR フィルター + 指定金額アラート。さらなる指標追加は `/optimize` の寄与度を見ながら継続。
- **frontend テストは単体中心**（Vitest で行整形・API クライアント・コンポーネント。ブラウザ E2E〔Playwright〕は未整備）。
- **スケジューラは API プロセス常駐前提**（祝日スキップは対応済み。OS 常駐は launchd/cron で・下記参照）。
- **祝日カレンダーは内蔵の簡易版**（2025〜2027 を収録。臨時休業や祝日法改正は毎年見直し前提）。
- **永続化・認証なし**（ローカル単一ユーザー前提。クラウド公開は想定外）。

### 🔜 これからやること（提案 / 優先度順）

1. **実データでチューニング**：`/optimize` で閾値スイープ・寄与度を見て、効かない指標は設定で重みを下げる/OFF。
2. **ブラウザ E2E テスト**：Playwright 等で主要フロー（更新→判定→作戦ボード）を自動化。
3. **指標のさらなる拡充**：`/optimize` の寄与度で取捨選択しながら追加。
4. **クラウド/マルチユーザー対応**：認証・永続化（必要になれば）。

> ⚠️ いずれも **自動売買は実装しない**（通知まで）方針は変えません。

---

### ディレクトリ構成

```
/backend   ... FastAPI + 計算ロジック (Python)
  signals.py          ... 指標計算 + 状態ベースのスコアリング evaluate()
  market.py           ... yfinance データ取得（+ 合成データ）
  backtest.py         ... ペーパートレード・バックテスト
  db.py               ... SQLite（§2 スキーマ・CRUD・app_meta 設定）
  scheduler.py        ... 日次自動更新スケジューラ（常駐スレッド・祝日スキップ）
  holidays_jp.py      ... 日本の市場休業日カレンダー（内蔵・2025〜2027）
  main.py             ... FastAPI エンドポイント（/optimize 含む）
  phase0_backtest.py  ... Phase 0 検証スクリプト（CLI）
  test_signals.py     ... 計算コアのスモークテスト
  test_api.py         ... API 結合テスト（FastAPI TestClient）
  test_scheduler.py   ... スケジューラのロジックテスト
/frontend  ... Next.js (TypeScript, App Router)
  src/lib/rows.ts     ... ダッシュボード行整形（純関数・テスト対象）
  src/**/__tests__    ... Vitest の単体/コンポーネントテスト
  vitest.config.mts   ... Vitest 設定
/data.db   ... SQLite（.gitignore 済み・初回起動時に自動生成）
/Makefile  ... 一括起動など
```

---

## クイックスタート（2プロセス）

このアプリは **Python API（:8000）** と **Next.js（:3000）** の2プロセス構成です。
**両方を起動**してください。

### セットアップ（初回のみ）

Python は **3.12 以上**、Node.js は 18 以上が必要です。

```bash
make setup
# 内訳:
#   python3.12 -m venv backend/venv
#   backend/venv/bin/pip install -r backend/requirements.txt
#   cd frontend && npm install
```

### 起動

```bash
make dev          # API と Next.js を一括起動（Ctrl-C で両方停止）
```

個別に起動する場合:

```bash
make backend      # = cd backend && venv/bin/uvicorn main:app --reload --port 8000
make frontend     # = cd frontend && npm run dev
```

ブラウザで **http://localhost:3000** を開く。
初回は設定画面で銘柄を確認し、ダッシュボードの「データ更新＋再判定」を押すと
価格取得・指標計算・シグナル保存が走ります。

> ネットワーク制限のある環境では、各画面の「demo（合成データ）」を ON にすると
> yfinance を使わずに動作確認できます。

---

## 画面

| 画面 | パス | 内容 |
|---|---|---|
| ダッシュボード | `/` | 監視銘柄一覧（TanStack Table）。現在値・本日スコア・出来高倍率・週足トレンド・判定を色分け。「データ更新＋再判定」ボタン |
| 作戦ボード | `/plan` | 翌営業日の判定・提案指値・利確/損切・根拠を一覧（追補版 強化4）。保有ポジション（取得単価・株数）を登録して含み損益・金額換算を表示。夜に見るメイン画面 |
| 銘柄詳細 | `/stocks/[ticker]` | ローソク足チャート（lightweight-charts）＋シグナル履歴 |
| 設定 | `/settings` | 銘柄の追加/削除、スコア閾値・自動更新、各指標の重み/ON-OFF/params、銘柄別のATR出口（利確×/損切×/指値方式）、指定金額アラートの追加・削除 |
| シミュレーション | `/simulation` | 期間・資金を指定してバックテスト → §4 成績表＋資産推移グラフ（recharts）。「ATR出口ルール」ON で出口入りシミュレーション（強化J） |
| 最適化 | `/optimize` | スコア閾値スイープと各指標の寄与度（leave-one-out）を自動評価し、推奨閾値を適用 |

### 通知（Phase 2）

ブラウザ通知 API を使用。初回アクセス時に通知許可を求めます。
`NotificationWatcher` が未通知シグナル（`/signals/unnotified`）を定期ポーリングし、
買い/売り/指定金額アラートが出たらデスクトップ通知します（**通知のみ・自動売買なし**）。

---

## Python API エンドポイント（:8000）

| メソッド | パス | 役割 |
|---|---|---|
| GET | `/watchlist` | 監視銘柄一覧 |
| POST | `/watchlist` | 銘柄追加 |
| DELETE | `/watchlist/{id}` | 銘柄削除 |
| GET | `/config` / PUT `/config` | signal_config の取得・更新（重み/有効/params） |
| POST | `/config` / DELETE `/config/{id}` | signal_config の追加・削除（指定金額アラート用） |
| GET | `/settings` / PUT `/settings` | スコア閾値・スケジューラ設定（`app_meta`） |
| GET | `/signals?ticker=8306.T` | シグナル履歴 |
| GET | `/signals/unnotified` | 未通知シグナル（通知用） |
| POST | `/signals/mark_notified` | 通知済みフラグ更新 |
| GET | `/prices/{ticker}` | ローソク足データ |
| GET | `/prices_latest` | 監視銘柄の最新終値（ダッシュボードの現在値用） |
| POST | `/refresh?demo=` | 最新データ取得＋再判定＋作戦ボード生成（全 enabled 銘柄） |
| GET | `/plan?date=` | 作戦ボード（指定日／省略時は最新） |
| POST | `/plan/generate?demo=` | 全 enabled 銘柄の翌日作戦ボードを生成・保存 |
| POST | `/backtest` | 期間・資金を受けてシミュレーション、§4 成績を返す（`exit_mode`: score/atr） |
| POST | `/optimize` | 閾値スイープ＋指標の寄与度（leave-one-out）を返すチューニング自動化 |
| GET/PUT | `/holdings` | 保有ポジション一覧／登録・更新（取得単価・株数。0以下で解除） |
| DELETE | `/holdings/{ticker}` | 保有ポジション削除 |

`?demo=true` / リクエストボディ `"demo": true` で合成データを使用します。

---

## Phase 0: 計算コアの検証（CLI）

`backend/phase0_backtest.py` が単一スクリプトで、yfinance 取得 → pandas-ta で
RSI / MACD / 移動平均クロス / ボリンジャーバンド / ストキャスティクス /
ローソク足パターン（赤三兵・三羽烏・包み足）を計算 → `evaluate()` で判定 →
直近約1ヶ月のペーパートレード（仮想資金3,000円・端株可）→ §4 成績を出力します。

```bash
make phase0                                   # yfinance から取得
make phase0 ARGS="--demo"                      # 合成データで検証
make phase0 ARGS="--tickers 8306.T 7203.T"     # 銘柄指定
make test                                      # オフライン スモークテスト
```

判定にはその営業日までのデータのみを使い、**未来データを使いません**（look-ahead bias 回避）。
スコア閾値（既定: 買い ≥ 2 / 売り ≤ -2）と各指標の重み・params は設定 UI（または `/settings`・
`/config`）から調整できます。スコアは状態ベース（トレンド系が常時 ±weight、逆張り系が
売られすぎ/買われすぎで加点）で、トレンド中の押し目買い・戻り売りが定常的に成立します。

---

## Phase 4: ペーパートレード運用

シミュレーション画面（または `/backtest`）で仮想資金3,000円・1ヶ月の成績を出し、
設定画面で各指標の重み・ON/OFF・params とスコア閾値を調整して「どの技が効くか」を詰めていきます。
「取引を記録」を ON にすると約定が `paper_trades` テーブルに保存されます。
日次自動更新を ON にすると、API 常駐中は毎営業日の指定時刻にまとめて判定・通知します（自動売買なし）。

---

## 依存・環境について

### Python 3.12 以上が必要

pandas-ta の現行版（0.4.x）が Python 3.12 を要求します（PyPI に 3.11 対応の旧 0.3 系がありません）。

### TA-Lib（ローソク足パターン用）

pandas-ta の `cdl_pattern`（赤三兵・三羽烏・包み足）は **TA-Lib** に依存します。
`requirements.txt` の `TA-Lib==0.6.8` は C ライブラリを同梱した wheel のため、
通常は `pip install` だけで導入できます。入らない環境では先に C ライブラリを入れてください:

```bash
brew install ta-lib                  # macOS
sudo apt-get install -y ta-lib       # Debian/Ubuntu（無い場合はソースビルド）
backend/venv/bin/pip install TA-Lib
```

### yfinance のネットワーク要件

yfinance は Yahoo Finance（`query1/query2.finance.yahoo.com`）にアクセスします。
**ローカル PC では通常そのまま動作します。** クラウド / サンドボックスなど egress 制限のある
環境では上記ホストがブロックされ取得に失敗します。その場合はホストを許可リストに追加するか、
各画面の「demo（合成データ）」/ CLI の `--demo` で計算ロジックの検証のみ行ってください。

### 自動更新スケジューラの常駐（macOS launchd）

設定画面の「日次自動更新」は **API プロセスが起動している間だけ** 動きます（毎営業日の指定時刻に
場後の refresh→判定→通知。祝日は `scheduler_skip_holidays` 既定 ON でスキップ）。
PC 起動中ずっと動かすには API（uvicorn）を常駐させます。launchd の例:

```xml
<!-- ~/Library/LaunchAgents/com.invest-analyzer.api.plist -->
<plist version="1.0"><dict>
  <key>Label</key><string>com.invest-analyzer.api</string>
  <key>WorkingDirectory</key><string>/path/to/invest-analyzer/backend</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/invest-analyzer/backend/venv/bin/uvicorn</string>
    <string>main:app</string><string>--port</string><string>8000</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.invest-analyzer.api.plist   # 常駐開始
launchctl unload ~/Library/LaunchAgents/com.invest-analyzer.api.plist # 停止
```

（cron 派は、毎営業日 16:10 に `curl -X POST localhost:8000/plan/generate` を叩く形でも代替できます。）

### frontend のテスト

```bash
cd frontend && npm test       # Vitest（行整形・API クライアント・コンポーネント）
```
