# 打ち手6: 連続確信度スコア＋Top N ランキング — 設計（spec）

- 日付: 2026-06-19
- ブランチ: `feature/continuous-confidence-topn`
- 対応課題: 課題3（横断比較・ランキング不在）、課題9（指標の ±1 二値化で強さ情報を破棄）
- ロードマップ: フェーズB／打ち手6（効果★★★・難易度 中）

## 1. 背景と問題

現状の `evaluate()` は銘柄ごとに整数 `score` と `direction`（buy/sell/neutral）を返すだけで、

- **課題9**: 各指標は閾値で ±weight に丸められる（RSI<30→+1、>70→−1 等）。RSI 29 と 15、MACD わずかプラスと急拡大が**同じ扱い**になり、確信度の解像度が落ちている。
- **課題3**: 作戦ボードは閾値超え銘柄を**全部並べる**だけで順位がない。「今夜張るべき最良の 2〜3 個」という選択（=作戦の核心）が無い。

打ち手6 はこの 2 つを、**連続確信度スコア（0–100）**と、それで並べた**Top N ランキング**で解消する。

## 2. ゴール / 非ゴール

### ゴール
1. 各指標の「強さ」を連続量として捕捉し（課題9）、銘柄ごとに **0–100 の量的確信度** `confidence` を算出する（課題3）。
2. 確信度で並べた **Top N**（既定3）を作戦ボード上部に「今夜の推奨」として切り出す。既存の全ウォッチ一覧は下に残す。
3. 既存の検証インフラ（backtest / holdout / 閾値 / レジームゲート / ベンチマーク）と direction 判定を**一切壊さない**。

### 非ゴール（スコープ外）
- **direction の連続化**: 方向決定は従来どおり整数 `score` の閾値投票を維持（将来打ち手で検証後に分離移行）。
- **RS（クロスセクション相対力）**: 打ち手7 の専任。確信度の構成要素に RS を加えるのは打ち手7（本設計は拡張点だけ残す）。
- **確信度の履歴蓄積・学習**: 打ち手11。

## 3. 主要な設計判断

### 採用案 B: 整数 score（方向）維持 ＋ 連続確信度を別チャネルで追加

| 案 | 内容 | 判定 |
|---|---|---|
| A | 整数 `score` 自体を連続値に置換し方向決定も連続化 | **不採用**。backtest/holdout/閾値/レジームゲート/volumeボーナスが全て整数 score 前提。検証インフラを揺らす。`test_evaluate_returns_valid_direction` の `isinstance(score,int)` を破壊。難易度「中」を超過 |
| **B** | 整数 `score`（方向）維持＋連続確信度 0–100 を `detail["confidence"]` に追加 | **採用**。打ち手3/4/5 と同じ加法的・後方互換パターン。課題9 の強さ情報を確信度チャネルで捕捉。検証インフラ無傷 |
| C | ランキングだけ先行（確信度=既存整数 score 代用） | 不採用。課題9 未対応で中途半端 |

**トレードオフの明示**: 案 B では direction 自体は離散投票のまま。課題9 の「連続化」は確信度チャネル（ランキング・確信度表示）で実現し、方向決定そのものの連続化は将来の検証課題として残す。これは「検証している作戦＝提示する作戦」を崩さない（フェーズA の原則）ことを優先した判断。

## 4. アーキテクチャ

データフロー（既存に確信度チャネルを加える）:

```
evaluate(df, configs, buy_th, sell_th, regime)
  → (score:int, direction:str, detail:dict)            # 戻り値シグネチャ不変
        detail["confidence"]  ← 新規（量的 0–100, float）
        detail["_strengths"]  ← 新規（解釈用: 指標別連続強度）
  → perform_refresh: daily_plan.confidence に保存（新カラム REAL）
  → GET /plan: rows に confidence を含めて返す
  → フロント作戦ボード: confidence 降順で Top N を上部に切り出し
```

### 4.1 連続指標強度 `s_i ∈ [-1, 1]`（符号: + = 買い寄り / − = 売り寄り）

各指標について、既存の ±weight 投票に加えて**符号付き連続強度**を算出する。基本原則:

- **オシレーター系（閾値型）**: 中立帯の外で、閾値から極限（0 や 100）に向けて線形に ±1 までランプ。
  - 例 RSI(low=30, high=70): `rsi ≤ low → s = +(low − rsi)/low`（rsi=30→0, rsi=0→+1）、`rsi ≥ high → s = −(rsi − high)/(100 − high)`、中立帯は 0。
  - これにより RSI 15（s≈0.5）> RSI 29（s≈0.03）で**単調**。stoch / cci / disparity も同型（各々の low/high と正規化幅で）。
- **モメンタム系（無界）**: 直近ボラティリティ（ATR）で正規化し `tanh` で有界化。
  - 例 MACDヒスト: `s = tanh(hist / (k · atr_proxy))`。ma_cross: `s = tanh((sma_short − sma_long) / (k · atr_proxy))`。
- **需給系（obv）**: `s = tanh((obv − obv_sma) / (k · |obv_sma|))`（符号 = obv vs sma）。
- **パターン系（candle）**: 離散のまま `s = ±1`（present のとき）。

`k` 等のスケール定数は `signals.py` の**名前付きモジュール定数**として定義（マジックナンバー散在を避ける）。強度の符号は既存 ±1 投票の符号と整合させる（同じ閾値・同じ向き）。

### 4.2 グループ集約とレジーム加重

既存の `INDICATOR_GROUP`（trend/contrarian/volume/pattern）と `_group_weight(regime, group)`（打ち手5 のレジーム別重み）を**そのまま再利用**する:

1. 指標強度をグループごとに合算し `s_g`、`[-GROUP_CAP, GROUP_CAP] = [-1, 1]` にクリップ（整数経路と同じ多重カウント抑止）。
2. 加重和 `A = Σ_g _group_weight(regime, g) · s_g`。
3. 正規化 `a = A / Wmax ∈ [-1, 1]`、ただし `Wmax = Σ_g _group_weight(regime, g) · GROUP_CAP`（全4グループ固定分母）。
   - 設計選択: 固定分母により**有効指標が少ない＝確信度が低め**になる（証拠が薄いほど自信を持たない）。これは妥当な保守側の挙動として採用する。

### 4.3 0–100 への写像と方向整合

```
raw = a                       # ∈ [-1, 1]
direction == "buy"     → conf = clip(raw, 0, 1) · 100
direction == "sell"    → conf = clip(−raw, 0, 1) · 100
direction == "neutral" → conf = 0           # Top N の対象外
```

その後、direction を**再決定しない**範囲で、確信度のみに以下の単一倍率（名前付き定数）を適用する（整数経路との二重計算回避のため evaluate 最終段＝gate 適用後に算出）:

- volume surge（`vr ≥ surge`）: `conf = min(100, conf · VOL_BOOST)`
- volume quiet（`vr < quiet`）: `conf = conf · VOL_DISCOUNT`
- レジームゲートが**減点 penalty**で発火（risk_off×buy 等）: `conf = conf · GATE_DISCOUNT`
- 週足フィルタが**逆行 penalty**で発火: `conf = conf · GATE_DISCOUNT`
- gate が **block** で direction を neutral 化した場合: 上の neutral 規則で `conf = 0`

最終 `confidence = round(conf, 1)`、範囲 `[0, 100]` を保証。

**二重計算の回避（実装上の明示）**: 既存 `evaluate` は volume フィルタ→`_direction`→週足 gate→レジーム gate の順で `score`/`direction` を**インラインで**書き換える。確信度はこの分岐ロジックに割り込ませず、**最終段で `detail` のキーを後から検査**して倍率を決める：`detail["volume"]`（surge は `±bonus`・quiet は `"quiet"`）、`detail["weekly_filter"]`、`detail["regime_filter"]`。これにより整数経路と確信度計算が独立し、二重計算を避ける。

**quiet volume の相互作用**: 整数経路の quiet は `score = int(score/2)` で borderline な score を閾値未満へ押し下げ、`direction` を neutral 化しうる。その場合は上の neutral 規則で `confidence = 0` となり VOL_DISCOUNT は無関係。VOL_DISCOUNT が効くのは **direction が生き残った（buy/sell のまま）quiet 時のみ**。

### 4.4 永続化と API

- `daily_plan` に `confidence REAL` を追加。既存 `_migrate_daily_plan` と同じ**冪等 ALTER**で旧 DB を移行（`ai_summary` 等と同じ手法）。
- `upsert_plan` / `list_plan` に `confidence` を通す。`perform_refresh` は `detail.get("confidence")` を保存。
- `GET /plan` の各 row に `confidence` が含まれる（`SELECT *` のため自動。ただし API テストで明示確認）。
- **名前衝突の整理**: 既存 `ai_confidence` は Gemini 解説の自己申告確信度（`ai_commentary.py`）。本設計の量的確信度は別物として `confidence` を用いる。フロント表示でも両者を区別（量的=「確信度」バッジ、AI=「AI解説／確信度」）。

### 4.5 Top N ランキング（フロント）

- `app_meta` に `top_n`（既定 `"3"`）を追加（`DEFAULT_META` と同じ key-value 機構、`get_meta`/`set_meta`）。`GET /settings`・`PUT /settings` に `top_n` を通す（既存 thresholds と同じ並び）。
- **`top_n` の検証/境界**: `buy_threshold` と同様に `int(get_meta("top_n", "3"))` でパースし、`max(0, n)` にクランプ（負値・非整数文字列は既定3にフォールバック）。`top_n` が actionable 件数より大きい場合は存在する分だけ表示（切り出し件数 = `min(top_n, actionable件数)`）。`top_n=0` は「今夜の推奨」セクション非表示（全ウォッチ一覧のみ）。
- 作戦ボード（`frontend/src/app/plan/page.tsx`）:
  - actionable（direction≠neutral かつ confidence 算出済み）な行を **confidence 降順**に並べ、上位 `top_n` を「**今夜の推奨 Top N**」セクションとして上部に切り出す。
  - 既存の全ウォッチ一覧は下にそのまま残す（課題3「ウォッチ一覧は残しつつ上部に作戦を切り出す」）。
  - 各カードに確信度バッジ（例「確信度 72」）を追加。
  - 同点タイブレーク: confidence 降順 → |score| 降順 → ticker 昇順（決定論的順序）。

## 5. 影響ファイル

- `backend/signals.py`: 連続強度算出、確信度合成、`evaluate` への `detail["confidence"]` 追加、スケール定数。
- `backend/db.py`: `daily_plan.confidence` カラム＋冪等マイグレーション、`upsert_plan`/`list_plan`、`app_meta` の `top_n` 既定。
- `backend/main.py`: `perform_refresh` で confidence 保存、`/settings` に `top_n`。
- `frontend/src/app/plan/page.tsx` ＋ `frontend/src/lib/api.ts`（`PlanRow.confidence`, settings 型）: Top N セクション・確信度バッジ。
- テスト: `backend/test_signals.py`（強度単調性・確信度範囲/方向整合/後方互換）、`backend/test_api.py`（/plan の confidence、/settings の top_n）、`backend/test_evaluation.py`（confidence が holdout/backtest を壊さない）、フロント `rows`/`api` テスト（Top N 切り出し・確信度表示）。

## 6. テスト戦略（TDD・ネット非依存）

- **連続強度の単調性**: `_declining_df` で RSI が深いほど強度が大きい（RSI 15 の強度 > 29）。決定論データ（`_idx`/`_declining_df`）のみ使用。
- **確信度の範囲と方向整合**: 上昇トレンド（`_idx(linspace up)`）で buy かつ confidence 高、下降で sell 側、全中立で 0。常に `0 ≤ confidence ≤ 100`。
- **後方互換**: `regime=None` および従来テスト群で `score`（int）・`direction` が不変。`detail["confidence"]` の追加のみ。`isinstance(score, int)` を維持。
- **gate 連動**: レジーム block で direction=neutral のとき confidence=0、penalty のとき confidence が割引される。
- **永続化/API**: `daily_plan.confidence` の保存と `/plan` 応答での露出、旧 DB へのマイグレーション冪等性、`/settings` の `top_n` 既定3・更新。
- **Top N（フロント）**: confidence 降順で上位 N を切り出し、同点タイブレークが決定論的、全ウォッチ一覧が残る。
- 既存 backend テスト（90件）が全てグリーンのまま。実行: `backend/venv/bin/python -m pytest backend/ -q`。

## 7. リスクと緩和

- **確信度と direction の不一致**: 確信度は最終 direction に整合させ（neutral→0）、二重計算を避けるため gate 適用後に算出することで緩和。
- **スケール定数の恣意性**: 名前付き定数に集約し、TDD で単調性・範囲・方向整合という**性質**を検証（特定値そのものではなく不変条件をテスト）。
- **旧 DB 移行**: 既存と同じ冪等 ALTER パターンで、`confidence` 欠如時は `None` を許容。

## 8. 完了の定義

- backend テスト全グリーン（既存＋新規、ネット非依存）。
- `/plan` が confidence を返し、作戦ボードが Top N を上部表示・各カードに確信度。
- 既存 direction / backtest / holdout の数値が不変（後方互換テストで保証）。
- final code-review 通過後、main へ FF マージ＋push。
