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
| **Phase 4** | 仮想資金3,000円で1ヶ月ペーパートレード運用・チューニング | ▶ 実行可能（運用フェーズ） |

---

## 進捗まとめ（やったこと / まだなこと / これからやること）

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

- **戦略チューニング（Phase 4 の本番運用）はこれから**。仕組みは完成しているが、
  「どの技が効くか・閾値や重みをどうするか」の実データ検証はまだ。初期値は全 weight=1 / 閾値 ±3。
- **実データでの動作確認が未了**。開発はネットワーク制限環境のため `demo`（合成データ）でのみ検証済み。
  実際の Yahoo Finance データでの挙動はローカル PC で要確認（下記「ネットワーク要件」）。
- **設定 UI で編集できるのは weight と ON/OFF のみ**。`params`（RSI 閾値や MA の期間など）と
  スコア閾値（buy≥3 / sell≤-3）はコード定数（`backend/signals.py`）で、UI からは未編集。
- **`price_target` を UI から追加する画面が未実装**（API/判定ロジックは対応済み。現状は DB 直挿入が必要）。
- **テストは backend のスモークテストのみ**。frontend の自動テスト・E2E は未整備。
- **自動更新スケジューラなし**（毎営業日の自動 refresh はしない。手動ボタン or 手動 cron 前提）。
- **永続化・認証なし**（ローカル単一ユーザー前提。クラウド公開は想定外）。

### 🔜 これからやること（提案 / 優先度順）

1. **実データで Phase 4 を回す**：ローカルで `make dev` → 実データ refresh → シミュレーションで
   1ヶ月成績を確認し、効く指標に weight を寄せる・閾値を調整する。
2. **設定 UI の拡張**：各指標の `params`（RSI の 30/70、MA の 5/25 など）とスコア閾値を UI から編集可能に。
3. **`price_target` の管理 UI**：銘柄ごとの上限/下限アラートを画面から追加・削除できるように。
4. **定期実行**：日次で自動 refresh（cron / スケジューラ）し、場後にまとめて判定・通知。
5. **指標の拡充と検証**：出来高系・ATR・乖離率などを追加し、バックテストで取捨選択。
6. **テスト整備**：API の結合テスト、frontend のコンポーネント/E2E テスト。

> ⚠️ いずれも **自動売買は実装しない**（通知まで）方針は変えません。

---

### ディレクトリ構成

```
/backend   ... FastAPI + 計算ロジック (Python)
  signals.py          ... 指標計算 + スコアリング evaluate()
  market.py           ... yfinance データ取得（+ 合成データ）
  backtest.py         ... ペーパートレード・バックテスト
  db.py               ... SQLite（§2 スキーマ・CRUD）
  main.py             ... FastAPI エンドポイント
  phase0_backtest.py  ... Phase 0 検証スクリプト（CLI）
  test_signals.py     ... オフライン スモークテスト
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
| ダッシュボード | `/` | 監視銘柄一覧（TanStack Table）。現在値・本日スコア・判定を色分け（買い=緑/売り=赤/中立=灰）。「データ更新＋再判定」ボタン |
| 銘柄詳細 | `/stocks/[ticker]` | ローソク足チャート（lightweight-charts）＋シグナル履歴 |
| 設定 | `/settings` | 銘柄の追加/削除、各指標の重み・ON/OFF 編集 |
| シミュレーション | `/simulation` | 期間・資金を指定してバックテスト → §4 成績表＋資産推移グラフ（recharts） |

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
| GET | `/signals?ticker=8306.T` | シグナル履歴 |
| GET | `/signals/unnotified` | 未通知シグナル（通知用） |
| POST | `/signals/mark_notified` | 通知済みフラグ更新 |
| GET | `/prices/{ticker}` | ローソク足データ |
| POST | `/refresh?demo=` | 最新データ取得＋再判定（全 enabled 銘柄） |
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
スコア閾値（買い ≥ 3 / 売り ≤ -3）と各指標の重み（初期値はすべて 1）は、
バックテスト結果を見て調整する前提です（`backend/signals.py`）。

---

## Phase 4: ペーパートレード運用

シミュレーション画面（または `/backtest`）で仮想資金3,000円・1ヶ月の成績を出し、
設定画面で各指標の重み・ON/OFF を調整して「どの技が効くか」を詰めていきます。
「取引を記録」を ON にすると約定が `paper_trades` テーブルに保存されます。

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
