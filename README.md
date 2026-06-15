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

仕様書のロードマップに沿って段階的に実装する。

| Phase | 内容 | 状況 |
|---|---|---|
| **Phase 0** | Python 計算コアの検証（yfinance + pandas-ta + バックテスト、UIなし） | ✅ 実装済み |
| Phase 1 | FastAPI + SQLite でAPI化・データ保存 | 未着手 |
| Phase 2 | ブラウザ通知 / 指定金額アラート | 未着手 |
| Phase 3 | Next.js UI（ダッシュボード・チャート・設定・シミュレーション） | 未着手 |
| Phase 4 | 仮想資金3,000円で1ヶ月ペーパートレード運用・チューニング | 未着手 |

最終的な構成（予定）:

```
/backend   ... FastAPI + 計算ロジック (Python)   ← 今ここ
/frontend  ... Next.js (TypeScript)               ← Phase 3
/data.db   ... SQLite（.gitignore 済み）          ← Phase 1
```

---

## Phase 0: 計算コアの検証

`backend/phase0_backtest.py` が単一スクリプトで以下を行う:

1. yfinance で複数銘柄（既定: 8306.T 三菱UFJ ほか）の日足を取得
2. pandas-ta で RSI / MACD / 移動平均クロス / ボリンジャーバンド / ストキャスティクス /
   ローソク足パターン（赤三兵・三羽烏・包み足）を計算
3. `signals.evaluate()` で重み付きスコアリング → `buy` / `sell` / `neutral` 判定
4. 直近約1ヶ月をペーパートレード（仮想資金3,000円・端株可）でバックテスト
5. 成績（開始資金・最終評価額・損益・取引回数・勝率・最大ドローダウン）をコンソール出力

判定にはその営業日までのデータのみを使い、**未来データを使わない**（look-ahead bias 回避）。

### セットアップ

Python **3.12 以上**が必要（pandas-ta 0.4.x の要件）。

```bash
cd backend
python3.12 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### 実行

```bash
# yfinance から実データを取得して検証
venv/bin/python phase0_backtest.py

# 銘柄を指定
venv/bin/python phase0_backtest.py --tickers 8306.T 7203.T 9984.T

# ネットワークを使わず合成データで計算ロジックだけ検証
venv/bin/python phase0_backtest.py --demo
```

出力例（`--demo`）:

```
--- 成績（§4 必須項目） ---
 開始資金        :        3,000 円
 最終評価額      :      3,061.4 円
 損益            :       +61.4 円 (+2.05%)
 取引回数        :           10 回  (うち決済 5 回)
 勝率            :        80.0%
 最大ドローダウン:        0.18%
```

> スコア閾値（買い ≥ 3 / 売り ≤ -3）と各指標の重み（初期値はすべて 1）は、
> バックテスト結果を見て調整する前提です（`signals.py` の `BUY_THRESHOLD` /
> `SELL_THRESHOLD` / `DEFAULT_CONFIGS`）。

---

## 依存・環境について

### TA-Lib（ローソク足パターン用）

pandas-ta の `cdl_pattern`（赤三兵・三羽烏・包み足）は **TA-Lib** に依存する。
`requirements.txt` の `TA-Lib==0.6.8` は C ライブラリを同梱した wheel のため、
通常は `pip install` だけで導入できる。

もし環境によって wheel が入らない場合は、先に C ライブラリを入れる:

```bash
# macOS
brew install ta-lib
# Debian/Ubuntu
sudo apt-get install -y ta-lib            # 提供がない場合はソースからビルド
# その後
venv/bin/pip install TA-Lib
```

### yfinance のネットワーク要件

yfinance は Yahoo Finance（`query1.finance.yahoo.com` / `query2.finance.yahoo.com`）に
アクセスする。**ローカル PC では通常そのまま動作する。**

クラウド / サンドボックスなど egress 制限のある環境では上記ホストが
ブロックされ取得に失敗する。その場合はホストを許可リストに追加するか、
`--demo`（合成データ）で計算ロジックの検証のみ行う。

---

## 起動構成（Phase 1 以降の予定・2プロセス）

```
Next.js (localhost:3000)  ──HTTP/JSON──▶  Python API (localhost:8000)
        UI                                  FastAPI + yfinance + pandas-ta
              └──────────── SQLite (data.db) ────────────┘
```

Phase 1 以降では Python API（`uvicorn main:app --reload`、:8000）と
Next.js（`npm run dev`、:3000）の **両方** を起動する。
（`concurrently` か Makefile での一括起動を予定）
