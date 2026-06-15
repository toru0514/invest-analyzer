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
  同時1ポジション）。提案指値（サポート基準）そのままでの約定や、資金共有の最適配分は今後の課題。
- **週足は日足からリサンプルして算出**（追補版の「週足を別 fetch・`timeframe` 列で保持」ではなく、
  同一日足データから週足化）。look-ahead を避けやすく追加の取得も不要なため。挙動（週足 SMA13 の傾き）は同じ。
- **追加指標の拡充は途中**（現状は 7 指標〔RSI/MA/MACD/BB/ストキャス/ローソク足/乖離率〕＋
  出来高/週足/ATR フィルター + 指定金額アラート。ATR・出来高系の追加指標化は今後）。
- **frontend の自動テストは未整備**（backend は計算コア/API 結合/スケジューラのテストあり。UI は手動/E2E 目視）。
- **スケジューラは API プロセス常駐前提**（プロセスを落とすと動かない。OS 常駐や cron 化は別途）。
- **永続化・認証なし**（ローカル単一ユーザー前提。クラウド公開は想定外）。

### 🔜 これからやること（提案 / 優先度順）

1. **実データでチューニング**：`make dev` → 実データ refresh → シミュレーションで成績を確認し、
   効く指標に weight を寄せる・閾値や params を設定 UI から調整する。
2. **指標の拡充と検証**：ATR 系・出来高系などをさらに追加し、バックテストで取捨選択。
3. **frontend テスト**：コンポーネント/E2E テスト（Playwright 等）の整備。
4. **スケジューラの堅牢化**：祝日カレンダー対応、launchd/cron での常駐化。

> ⚠️ いずれも **自動売買は実装しない**（通知まで）方針は変えません。

---

### ディレクトリ構成

```
/backend   ... FastAPI + 計算ロジック (Python)
  signals.py          ... 指標計算 + 状態ベースのスコアリング evaluate()
  market.py           ... yfinance データ取得（+ 合成データ）
  backtest.py         ... ペーパートレード・バックテスト
  db.py               ... SQLite（§2 スキーマ・CRUD・app_meta 設定）
  scheduler.py        ... 日次自動更新スケジューラ（常駐スレッド）
  main.py             ... FastAPI エンドポイント
  phase0_backtest.py  ... Phase 0 検証スクリプト（CLI）
  test_signals.py     ... 計算コアのスモークテスト
  test_api.py         ... API 結合テスト（FastAPI TestClient）
  test_scheduler.py   ... スケジューラのロジックテスト
/frontend  ... Next.js (TypeScript, App Router)
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
| 作戦ボード | `/plan` | 翌営業日の判定・提案指値・利確/損切・根拠を一覧（追補版 強化4）。夜に見るメイン画面 |
| 銘柄詳細 | `/stocks/[ticker]` | ローソク足チャート（lightweight-charts）＋シグナル履歴 |
| 設定 | `/settings` | 銘柄の追加/削除、スコア閾値・自動更新、各指標の重み/ON-OFF/params、指定金額アラートの追加・削除 |
| シミュレーション | `/simulation` | 期間・資金を指定してバックテスト → §4 成績表＋資産推移グラフ（recharts）。「ATR出口ルール」ON で出口入りシミュレーション（強化J） |

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
| POST | `/backtest` | 期間・資金を受けてシミュレーション、§4 成績を返す |

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
