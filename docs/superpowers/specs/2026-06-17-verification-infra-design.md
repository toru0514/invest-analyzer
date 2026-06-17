# 検証インフラ刷新（フェーズA）設計書

- 日付: 2026-06-17
- 対象ブランチ: `feature/verification-infra`
- 対応課題: `課題.md` 課題4（検証が過学習しやすく「改善した」を信用できない）／打ち手1・打ち手2
- ステータス: 設計（実装前）

---

## 1. 背景と問題

`課題.md` のロードマップは「フェーズA：検証インフラを正す」を最優先（**これ無しに先へ進めない**）と位置づける。理由は、地合い・スコア再設計・出口などをどれだけ改善しても、現在の検証では「本当に良くなったか」を判定できないため。

現状のコードで確認した事実：

| 観点 | 現状 | 根拠 |
|---|---|---|
| データ期間 | `get_history` 既定 `period="6mo"`。warmup35日＋eval22〜40日でトレード数ごく少数 | `market.py:13,48`、`backtest.py:15-16` |
| 売買コスト | 未モデル。終値/指値ぴったりで約定 | `backtest.py:57,73,159,177` |
| 最適化 | `/optimize` が同一データで閾値2/3/4を試し最良を採用（in-sample カーブフィッティング） | `main.py:512-518` |
| 検証=提示の不一致 | ATR検証は `close-0.5ATR` で約定、作戦ボードは build_plan の指値。**検証する作戦≠提示する作戦** | `backtest.py:197-200` がコメントで自認 |
| 統計的有意性 | トレード数・標準誤差・誤差範囲警告なし | — |

## 2. ゴール / 非ゴール

### ゴール（本設計の対象＝打ち手1＋2を一括）
1. **データ期間を3年（可変）に拡張**し、十分なトレード数を確保する。
2. **売買コスト（手数料・スリッページ）を必ず織り込む**。期待値はコスト込みで判断する。
3. **検証する作戦＝提示する作戦に統一**する（バックテストの約定を `build_plan` の提案指値・stop・target に一致させる）。
4. **過学習を防ぐ out-of-sample 評価（シンプルホールドアウト2段構え）**を導入する。
5. **統計的有意性を併記**する（トレード数・標準誤差・「誤差範囲」警告）。
6. **ベンチマーク比較**（buy&hold／全シグナル等加重）で「選別が効いているか」の土台を作る。

### 非ゴール（将来課題・本設計では扱わない）
- ローリング walk-forward（本設計はシンプルホールドアウト。インターフェースは将来拡張可能に保つ）。
- 価格データの永続キャッシュ最適化（v1は `period` 指定取得。重い場合の最適化は将来）。
- 流動性フィルター・データ健全性チェック（課題8）。
- リスクベースのサイジング（課題5・打ち手8）。
- 自動売買・発注（恒久的に非対象）。

## 3. 確定した設計判断（ユーザー合意済み）

| 項目 | 決定 |
|---|---|
| スコープ | 打ち手1＋2を一つの設計で（一括） |
| コスト想定 | 手数料 **0**（ネット証券ゼロコース）＋スリッページ **片道0.1%（10bps）**。**設定で可変** |
| 評価方式 | **シンプルホールドアウト2段構え**（前半 in-sample で選定 → 後半 out-of-sample で評価） |
| 実装アプローチ | **ハイブリッド**：`run_backtest` を単一窓エンジンとして活かし、欠けている層（コスト／約定統一／ホールドアウト／統計）だけ追加 |
| データ期間 | 既定 **3年**（`period="3y"`・可変） |

## 4. アーキテクチャ（モジュール構成）

リポジトリの既存スタイル（`backend/` 直下のフラットモジュール）に合わせ、責務を分割する。

| モジュール | 区分 | 責務 | 主な関数 |
|---|---|---|---|
| `costs.py` | 新規 | 売買コストの適用（純粋関数・小さい・依存なし） | `apply_costs(price, side, cost) -> float`、`commission_cost(notional, cost) -> float`、`DEFAULT_COST` |
| `backtest.py` | 改修 | **単一窓**シミュレータ。コスト＋約定統一を内蔵 | `run_backtest(...)`（plan基準を主・score基準をベースライン） |
| `evaluation.py` | 新規 | **out-of-sample 評価層** | `evaluate_holdout(...)`、`summary_stats(...)`、`benchmark(...)` |
| `main.py` | 改修 | `/backtest`・`/optimize` を上記の薄い呼び出しに | — |

**分割の狙い**：`costs`/`evaluation` を `backtest` から独立に単体テストできること、`main.py` を薄く保つこと。各モジュールは「何をするか・どう使うか・何に依存するか」が一文で言えること。

## 5. コンポーネント詳細

### 5.1 コストモデル（`costs.py`）

```
cost = {"commission_bps": 0.0, "slippage_bps": 10.0}   # 片道。bps = 0.01%
```

- 保存：`signal_config` に新 `rule_type: "cost_model"`、`params={"commission_bps":0,"slippage_bps":10}`。`/config` で可変。未設定時は `DEFAULT_COST`。
- 約定価格補正：
  - 買い: `fill = price * (1 + slippage_bps/1e4)`
  - 売り: `fill = price * (1 - slippage_bps/1e4)`
- 手数料：各レグで `notional * commission_bps/1e4` を現金から差引（買い時・売り時の両方）。
- 結果として、損益・期待値はすべて**コスト込み**で算出される。`slippage_bps=0, commission_bps=0` のとき従来と同値（ゼロコスト同値性をテストで担保）。

### 5.2 約定モデルの統一（検証=提示）

`backtest.py` の plan 基準シミュレーション（現 `_run_backtest_atr` 相当）を以下に統一する：

1. **意思決定**：当日終値までで `evaluate()`。buy なら `build_plan(window, "buy", score, configs)` を呼び、**作戦ボードと同一の** `limit_price` / `stop_price` / `target_price` を得る。
2. **発注**：`limit_price` に GTC 指値。有効期限 = `entry_expiry_days`（既定 **5**・可変）。
3. **約定**：期限内に **当日安値 ≤ `limit_price`** で約定。約定価格はコスト適用後（`apply_costs`）。期限内に未到達なら **未約定＝トレードなし**。
4. **手仕舞い**：保有中に **安値 ≤ `stop_price`（損切）** / **高値 ≥ `target_price`（利確）** / **sellシグナル（終値）** で決済（コスト適用）。損切り優先。
5. `close - 0.5*ATR` の特例は**廃止**。`limit_method`（ma/support/atr）はライブと同一設定を使用。
   - 注：既定 `limit_method="ma"` の buy は `min(ma_val, close)`（`signals.py:451`）なので、指値が当日終値とほぼ同値になり**即日に近い約定が増える**。これは旧 `close-0.5ATR`（押し下げ）からの意図的な挙動変更で、「検証=提示」の核心。テストで `ma` 方式の約定挙動を固定する（§9）。
6. **約定率 `fill_rate`**（発注数に対する約定数）を成績に併記。

> look-ahead 回避は現状を踏襲：判定は当日終値まで、執行は翌日以降の OHLC のみ参照（`backtest.py:133` の原則を維持）。

### 5.3 ホールドアウト評価器（`evaluation.py:evaluate_holdout`）

```
evaluate_holdout(histories, configs, *, split_ratio=0.7, grid=DEFAULT_GRID,
                 cost=DEFAULT_COST, exit_mode="plan", initial_capital, warmup_days) -> dict
```

- **分割**：各銘柄の日付範囲を時系列で train=前半 `split_ratio`／test=後半 `1-split_ratio` に分ける。warmup は各区間内で確保。train と test の評価期間は**時系列で重ならない**。
- **既定グリッド（具体化）**：
  ```
  DEFAULT_GRID = {"threshold": [2, 3, 4]}   # buy=+t / sell=-t。exit_mode は "plan"（検証=提示）に固定
  ```
  v1 では閾値のみを探索する（現行 `/optimize` の閾値スイープに対応）。`limit_method`・`entry_expiry_days` 等は探索せず既定固定（YAGNI、将来拡張）。score モードは最適化対象に含めず、ベンチマーク的に別途併記してよい。
- **in-sample 選定**：`DEFAULT_GRID` を train 上で `run_backtest` し、**コスト込み期待値**が最大のパラメータを選ぶ（同点は trade数・勝率で）。
- **out-of-sample 評価**：選んだパラメータで test を `run_backtest`。**OOS の期待値が見出し数値**。
- **過学習ギャップ**：`in_sample_expectancy - oos_expectancy` を併記（大きく落ちる＝過学習の警告フラグ）。
- 戻り値：`{chosen_params, in_sample:{...}, out_of_sample:{...}, overfit_gap, significance, benchmark}`。

### 5.4 統計サマリ（`evaluation.py:summary_stats`）

- 入力：クローズ済みトレードの損益リスト（コスト込み）。
- 出力：`{n, avg_return, std_error, expectancy, win_rate, insufficient}`。
  - `std_error = stdev / sqrt(n)`
  - `expectancy = win_rate*avg_win - (1-win_rate)*avg_loss`（コスト込みなので追加控除なし）
  - `insufficient = (n < 30)` → 「統計的に不十分（誤差範囲）」警告。
- n=0/1 の境界は安全に扱う（std_error は n<2 で None、insufficient=True）。

### 5.5 ベンチマーク（`evaluation.py:benchmark`）

- **(a) ユニバース等加重 buy&hold**：test 期間の頭で全銘柄を等加重で買い持ち、末で評価。
- **(b) 全シグナル等加重（選別なし）**：閾値を超えた全シグナルを等加重で約定（Top N 選別をしない素のシグナル運用）。
- 戦略の OOS リターンが (a)(b) を上回るかを併記（課題3「選別が効いているか」の検証土台）。

## 6. API 変更

### 6.1 `/backtest`（単一窓・コスト＋約定統一）
- `BacktestIn` に `period: str = "3y"` を追加（`days` は任意。省略時は取得期間からwarmupを除いた全体）。
- 応答に追加（既存キーは保持）：`cost`（使用パラメータ）、`fill_rate`、`significance`（n・std_error・insufficient）、`benchmark`。

### 6.2 `/optimize`（ホールドアウト2段構え）
- `OptimizeIn` に `period: str = "3y"`、`split_ratio: float = 0.7` を追加。
- 内部を `evaluate_holdout` 呼び出しに置換。応答は in-sample と out-of-sample を**明確に分離したフィールド**で返す（見出し誤読防止）：
  ```
  {
    "in_sample":  {"sample": "in_sample", "chosen_params": {...},
                   "sweep": [...], "contributions": [...],
                   "best": {...}, "baseline_pnl_pct": ..., "expectancy": ..., "pnl_pct": ...},
    "out_of_sample": {"sample": "out_of_sample",            // ← 見出し（このアプリの真の成績）
                      "expectancy": ..., "pnl_pct": ..., "win_rate": ...,
                      "trade_count": ..., "fill_rate": ...},
    "overfit_gap": in_sample.expectancy - out_of_sample.expectancy,
    "significance": {...},   // OOS トレードに対して
    "benchmark": {...},      // OOS 期間
    "failed": [...], "tickers": [...]
  }
  ```
- 既存の `sweep` / `contributions` / `best` / `baseline_pnl_pct` は **`in_sample` 配下に移動**（train 上で計算）。各々 `sample:"in_sample"` の文脈に属し、トップレベル見出しにはしない。フロントは `out_of_sample` を見出しとして表示する。

### 6.3 共通
- demo モード（合成データ）は引き続き動作（ネット非依存テストを維持）。

## 7. データフロー

```
/optimize(period=3y, split=0.7)
  → get_history(各銘柄, period=3y)
  → evaluate_holdout
       ├ train窓: grid を run_backtest(cost) で総当り → 最良パラメータ選定（in-sample）
       ├ test窓 : 最良パラメータで run_backtest(cost) → OOS 期待値（見出し）
       ├ summary_stats(test trades) → n・標準誤差・誤差範囲フラグ
       └ benchmark(test窓) → buy&hold / 全シグナル等加重
  → {in_sample, out_of_sample, overfit_gap, significance, benchmark}
```

## 8. 後方互換

- 既存応答キーはすべて保持し、新キーを追加する。
- 約定モデル統一により atr/plan モードの損益値は変わる（`close-0.5ATR`→build_plan指値）。キー存在・範囲を検証するテストは維持し、**約定挙動に依存する数値アサーションのみ更新**。score モードは不変。
- コスト既定（手数料0・スリッページ10bps）により、コスト未指定でも従来比でわずかに損益が下がる（スリッページ分）。これは正しい挙動。

## 9. テスト戦略（決定論・ネット非依存・demo/合成データ）

- `costs.py`：買い/売り方向の補正、手数料計算、ゼロコスト同値性。
- `backtest.py`：指値到達で約定（指値＋スリッページ）、未到達＆期限切れで未約定、stop/target 決済、`fill_rate` 計算、コストで pnl が下がること。
  - **既定 `limit_method="ma"` の約定挙動を固定**：指値が当日終値とほぼ同値になり即日に近い約定が起きること（旧 `close-0.5ATR` の押し下げが廃止されたこと）を明示的に検証する（§5.2注）。
- `evaluation.py`：
  - ホールドアウト分割が時系列で重ならない／test のパラメータは train のみから選ばれる（look-ahead無し）。
  - `summary_stats` の標準誤差・`n<30` 警告。
  - `benchmark`（buy&hold／全シグナル等加重）の計算。
- 結合（`test_api.py`）：`/backtest`・`/optimize` が demo で新キーを返す、OOS 指標が存在、コストで pnl ≤ ゼロコスト pnl。
- 既存 44 件は維持（意味が変わる数件のみ更新）。

## 10. エラー処理・境界

- `cost_model` 未設定 → `DEFAULT_COST`。
- ゼロトレード／test 窓が空 → `significance.insufficient=True`、クラッシュなし、見出しは「データ不足」。
- 履歴が `warmup + 最小評価日数` に満たない → 明確なエラー or スキップ（`failed` に計上）。
- yfinance 取得失敗 → 既存どおり空 DataFrame → `failed`。`demo=true` で合成データ。

## 11. 成功基準（本設計のスコープ内）

- `/optimize` が **in-sample で選び out-of-sample で評価する2段構え**になっている。
- すべての損益・期待値が**コスト込み**で算出される。
- バックテストの約定が **`build_plan` の提示指値・stop・target に一致**する（検証=提示）。
- 成績に **トレード数・標準誤差・誤差範囲警告** が併記される。
- **ベンチマーク（buy&hold／全シグナル等加重）との比較**が出る。
- 既存テストが緑、追加テストが新ロジックを網羅。

> ⚠️ 本設計は検証の信頼性を上げるものであり、利益を保証しない。投資は自己責任の原則は変えない。
