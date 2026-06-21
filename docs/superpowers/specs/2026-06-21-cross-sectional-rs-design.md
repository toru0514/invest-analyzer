# 打ち手7: クロスセクション相対力（RS）— 設計（spec）

- 日付: 2026-06-21
- ブランチ: `feature/cross-sectional-rs`
- 対応課題: 課題3（横断比較・ランキング不在＝相対力未実装）、課題2（地合い＝指数対比の相対力）
- ロードマップ: フェーズC 入口の前哨／打ち手7（効果★★・難易度 中）

## 1. 背景と問題

スイングの王道は **「強い銘柄を買い、弱い銘柄を売る」** ＝相対力（Relative Strength, RS）。現状の `evaluate()` は各銘柄を**完全に独立**に評価し、指数（地合い）や他銘柄との**相対的な強弱**を一切見ていない。

- **課題3**: 「直近 N 日騰落率」のような相対力でのランキング軸が無い。確信度（打ち手6）は各銘柄内の指標強度だけで決まり、「市場・指数に対してこの銘柄が強いか」が入っていない。
- **課題2**: 「指数対比の相対力（RS）で個別を評価する」と明示されているが未実装。

打ち手6 は確信度 `_strength_net` の `_CONF_GROUPS`（現状 `trend/contrarian/volume/pattern` の固定4グループ）に **RS グループを足す拡張点**を意図的に残してある（roadmap-progress メモ）。打ち手7 はその拡張点を埋める。

## 2. ゴール / 非ゴール

### ゴール
1. 各銘柄について **指数対比の相対力 RS**（直近 N 日の超過リターン）を符号付き連続強度 `s_rs ∈ [-1, 1]` として算出する（+ = 指数をアウトパフォーム＝強気寄り）。
2. RS を**確信度チャネルにのみ**加える（`_strength_net` の新グループ `rs`）。整数 `score`/`direction` は不変。これにより既存 Top N ランキング（打ち手6）が相対力を織り込んで並ぶ。
3. 既存の検証インフラ（backtest / holdout / 閾値 / レジームゲート / ベンチマーク）と direction 判定、および**既存の確信度数値**を、RS 未供給時は**一切壊さない**（後方互換）。
4. refresh・`run_backtest`・`_run_backtest_plan` の3経路すべてで **look-ahead 安全**に RS を供給できる。

### 非ゴール（スコープ外）
- **整数 score / direction への RS 反映**: 方向決定は従来どおり指標投票を維持（打ち手6 と同じ加法的・後方互換の案B）。RS で買い/売り判定そのものを変えることはしない。
- **監視銘柄群内のクロスセクション・パーセンタイル順位**: 後述（§3）の理由で v1 では採用せず、将来拡張として明記。
- **RS 専用の UI 追加**: 確信度（既存バッジ・Top N）経由で反映されるため、新規 UI コンポーネントは作らない。`detail` に RS を出して既存表示で読める形にとどめる（フロントは確信度の数値変化として現れる）。
- **業種・テーマ別 RS**: スコープ外（将来のセクター連動・課題3.5）。

## 3. 主要な設計判断

### 採用案: 「指数対比の超過リターン」を RS の核とし、確信度へ加法的に注入

| 案 | 内容 | 判定 |
|---|---|---|
| **B（採用）** | RS = 銘柄の N 日リターン − 指数の N 日リターン。`tanh` で `s_rs ∈ [-1,1]` に。確信度 `_strength_net` の新グループ `rs` に加える。score/direction 不変。RS 未供給時は従来挙動 | **採用**。3経路すべてで同型・look-ahead 安全に計算でき、小さな監視銘柄群でも頑健。打ち手6 が残した拡張点に正対 |
| A | RS = 監視銘柄群内の N 日騰落率パーセンタイル順位 | **不採用**。(1) 銘柄外側ループの `_run_backtest_plan` にクロスセクション障壁を生み look-ahead 実装が複雑化。(2) 監視銘柄が数件だとパーセンタイルが粗くノイジー。(3) 「弱い銘柄群の中の最強」が高 RS になる絶対水準の歪み |
| C | RS を整数 score にも加点し direction を動かす | 不採用。検証インフラ（backtest/holdout/閾値）の不変性と「検証=提示」を崩す。難易度超過 |

**トレードオフの明示**:
- 案B の RS は**指数（ベンチマーク）対比の絶対的な相対力**で、「監視銘柄群の中での順位」ではない。課題3 の「監視銘柄群・指数に対するリターン順位」のうち**指数対比**を実装し、銘柄群内パーセンタイルは将来拡張とする。これは look-ahead 安全性・小ユニバースでの頑健性・3経路の実装単純性を優先した判断。
- 銘柄群内パーセンタイルは、`run_backtest`（日付外側ループ）では各日に全銘柄窓が揃うため実装可能だが、`_run_backtest_plan`（銘柄外側ループ）では事前パスが必要で、確信度しか動かさないわりにコストが高い。v1 では見送る。

## 4. アーキテクチャ

データフロー（既存に RS 強度を1つ加える）:

```
relative_strength(ticker_window, index_window, n, scale) → s_rs ∈ [-1, 1]   # 新規ヘルパ
  └ 呼び出し側が window を日付で切って look-ahead を担保（regime_series と同じ流儀）

evaluate(df, configs, buy_th, sell_th, regime, rs_strength=None)            # 引数1つ追加
  → _score_indicators(df, configs, regime, rs_strength)
        detail["_strengths"]["rs"]   ← s_rs（供給時のみ）
        detail["_strength_net"]      ← _CONF_GROUPS に rs を含めて再正規化（供給時のみ）
        detail["rs"]                 ← round(s_rs, 3)（解釈用・供給時のみ）
  → (score:int, direction:str, detail)   # 戻り値シグネチャ・score・direction は不変
  → detail["confidence"] に rs が織り込まれる（打ち手6 の最終段はそのまま）

呼び出し側で window を渡して RS を計算:
  - perform_refresh: 既取得 idx_df（^N225）から銘柄ごとに rs_strength を算出して evaluate へ
  - run_backtest / _run_backtest_plan: index_history を任意引数で受け、各評価日の窓で算出
```

### 4.1 RS 強度 `s_rs ∈ [-1, 1]`

```
r_ticker = close_ticker[t] / close_ticker[t-n] − 1     # 銘柄の n 日リターン
r_index  = close_index[t']  / close_index[t'-n]  − 1     # 指数の n 日リターン（t' = t 以前で最も近い指数営業日）
excess   = r_ticker − r_index                            # 超過リターン
s_rs     = _tanh_strength(excess, RS_STRENGTH_SCALE)     # tanh で有界化（既存ヘルパ再利用）
```

- 符号: `excess > 0`（指数より強い）→ `s_rs > 0`（買い寄り）。`excess < 0` → 売り寄り。これは「強い銘柄を買い、弱い銘柄を売る」に整合。
- `RS_STRENGTH_SCALE` は名前付きモジュール定数（既定 `0.10` ＝ 20 日で指数を +10% アウトパフォームすると `s_rs ≈ tanh(1) ≈ 0.76`）。マジックナンバー散在を避ける（打ち手6 の MACD_STRENGTH_ATR_K 等と同じ流儀）。
- 既存 `_tanh_strength(x, scale)`（`signals.py:300`）をそのまま再利用。

**ヘルパ `relative_strength(ticker_df, index_df, n, scale)`**:
- 戻り値 `float | None`。データ不足（どちらかが `n+1` 本未満）や `close[t-n] <= 0` → `None`（RS 無効＝後方互換でその銘柄は従来確信度）。
- 指数の日付整合: `index_df` は ticker と営業日がズレうるため、`index_df` を昇順に並べ、**末尾（最新）と n 本前**を用いる。呼び出し側が `index_df` を「評価日以前」に切って渡すことで look-ahead を担保（`regime_series`/`market_regime` と同じ責務分担：窓を切るのは呼び出し側）。
- 純粋関数（ネット非依存）。`market.py` ではなく `signals.py` に置く（指標系と同居）。

### 4.2 確信度への注入（`_CONF_GROUPS` の可変化）

打ち手6 の `_score_indicators` 末尾（`signals.py:456-469`）を最小拡張する:

1. `_score_indicators(df, configs, regime, rs_strength=None)` に引数追加。
2. `rs_strength is not None` のとき `strengths["rs"] = rs_strength`、`sgroup_raw["rs"] = rs_strength` を加える（`±GROUP_CAP` クリップは他グループと同様）。
3. **`_CONF_GROUPS` を動的化**:
   ```
   _CONF_GROUPS = ("trend", "contrarian", "volume", "pattern")
   if rs_strength is not None:
       _CONF_GROUPS = _CONF_GROUPS + ("rs",)
   ```
   - `rs_strength is None` のとき分母 `wmax`・分子 `anum` に "rs" は**入らない**＝既存の4グループ確信度を**完全保存**（既存テスト・既存 DB 値が不変）。
   - 供給時のみ5グループ目として正規化に参加。固定分母方針（証拠が薄いほど保守的）は維持：RS が供給されれば分母が増え、RS 強度が弱い銘柄は相対的に確信度が下がる（妥当な保守側挙動）。
4. `detail["rs"] = round(rs_strength, 3)`（供給時のみ・解釈用）。整数 `score` は一切触らない。

### 4.3 レジーム別 RS 重み

`REGIME_GROUP_WEIGHTS`（`signals.py:261`）に `rs` キーを追加する。RS はモメンタム／トレンド追随の概念なので **trend と同じ重み付け**を採用:

```
risk_on:  {..., "rs": 2}     # 上昇地合いでは相対力（強い銘柄を買う）を重視
neutral:  {..., "rs": 1}
risk_off: {..., "rs": 1}     # 下落地合いでは相対力の買い寄与を抑える（落ちるナイフ抑制は別途レジームゲート）
```

- `_group_weight(regime, "rs")` は未知レジームで 1（既存の安全フォールバックを流用）。
- `regime=None`（重みなし）のときは全グループ 1＝RS も等価重みで確信度に参加。

### 4.4 呼び出し側の配線（look-ahead 安全）

**perform_refresh**（`main.py:340-374`）:
- 既に `idx_df = get_history(index_ticker, ...)` を取得済み（L356）。RS 設定 `relative_strength` を common から引く。
- 各銘柄ループ内で `rs = relative_strength(df, idx_df, n, scale)` を計算し `evaluate(..., rs_strength=rs)` へ。
- `idx_df` 取得失敗時は `rs=None`（best-effort・従来確信度）。

**run_backtest**（`backtest.py:62-67`・日付外側ループ）:
- `run_backtest(..., index_history=None)` を任意引数で追加。各評価日 `d` で `evaluate(window=df.loc[:d], ..., rs_strength=relative_strength(window, index_history.loc[:d], n, scale))`。
- `index_history is None` → `rs_strength=None`（従来どおり）。

**_run_backtest_plan**（`backtest.py:200-203`・銘柄外側ループ）:
- 同様に `index_history` を受け、`window = df.iloc[:i+1]` に対して `index_history` を当該日付以前に切って RS を算出。
- 末尾再判定（L228-229）にも同様に供給。

**RS 設定の供給**: `_fetch_regime_series`（`main.py:51-58`）が既に index を取得しているのと同様に、backtest 呼び出し箇所（`/optimize`・`/backtest`）で index history を取得して `index_history=` に渡す。取得失敗・未設定時は `None` で RS 無効（後方互換）。

### 4.5 設定

`DEFAULT_CONFIGS`（`signals.py:26`）に common ルールを追加（既存 `market_regime` と同じ流儀）:

```python
{"rule_type": "relative_strength",
 "params": {"period": 20, "scale": 0.10, "enabled": 1},
 "weight": 1, "enabled": 1},
```

- `period`（N 日・既定 20）・`scale`（tanh 正規化幅・既定 0.10）。
- ルール `enabled: 0` または設定不在 → RS 計算をスキップ（`rs_strength=None`）。
- 呼び出し側は `_find_cfg(common, "relative_strength")` で params を引き、`enabled` を見て RS の ON/OFF を決める（`volume_filter`/`market_regime` と同じパターン）。

## 5. 影響ファイル

- `backend/signals.py`:
  - `relative_strength(ticker_df, index_df, n, scale)` ヘルパ新規。
  - `RS_STRENGTH_SCALE` 定数、`REGIME_GROUP_WEIGHTS` に `rs` 追加。
  - `_score_indicators` に `rs_strength` 引数＋`_CONF_GROUPS` 動的化＋`detail["rs"]`。
  - `evaluate` に `rs_strength` 引数（_score_indicators へ素通し）。
  - `DEFAULT_CONFIGS` に `relative_strength` ルール。
- `backend/main.py`: `perform_refresh` で RS 計算・evaluate へ供給。`/optimize`・`/backtest` で index history を取得し backtest へ渡す。
- `backend/backtest.py`: `run_backtest`・`_run_backtest_plan` に `index_history` 任意引数、各評価日で RS を算出して evaluate へ。
- テスト: `backend/test_signals.py`（RS 強度の符号・単調性・None 後方互換・確信度への寄与）、`backend/test_backtest.py`（index_history 供給時／None 時の不変性）、必要なら `backend/test_api.py`（refresh で RS が確信度に反映され例外なく動く）。
- フロント: 新規コンポーネントなし。確信度バッジ・Top N は既存のまま（RS は confidence 数値として反映）。`detail.rs` を表示に出すかは任意（v1 は出さなくてよい）。

## 6. テスト戦略（TDD・ネット非依存）

決定論データ（`_idx`/`_declining_df` ＝ [[signal-tests-prefer-deterministic-ohlc]]）で性質を検証する。

- **`relative_strength` の符号**: 銘柄が指数より強く上昇（excess>0）→ `s_rs>0`。逆 → `s_rs<0`。同一推移 → ≈0。
- **単調性**: excess が大きいほど `|s_rs|` が大（tanh の単調性）。
- **データ不足/ゼロ除算**: `n+1` 本未満・`close[t-n]<=0` → `None`。
- **後方互換（最重要）**: `rs_strength=None`（既定）で `evaluate` の `score`・`direction`・`detail["confidence"]` が**現状と完全一致**（既存テスト・既存確信度数値が不変）。`isinstance(score, int)` 維持。
- **確信度への寄与**: buy 方向の銘柄に正の `rs_strength` を与えると `confidence` が（RS 無し比で）上がり、負を与えると下がる。`0 ≤ confidence ≤ 100` を常に保証。
- **レジーム重み**: `risk_on` で rs の確信度寄与が `neutral`/`risk_off` より大（rs 重み 2 vs 1）。
- **5グループ正規化**: rs 供給時に `_strength_net` の分母が5グループ分になり、他グループが同じでも値が変わる（正規化が効いている）こと。
- **backtest 不変性**: `index_history=None` で `run_backtest`/`_run_backtest_plan` の結果が現状と一致。供給時は例外なく走り、confidence が変化しても **closed PnL・direction ベースの売買は不変**（RS は score/direction を動かさないため）。
- 既存 backend テスト（99件）が全てグリーン。実行: `backend/venv/bin/python -m pytest backend/ -q`。

## 7. リスクと緩和

- **既存確信度数値の破壊**: `rs_strength=None` で "rs" を分母から完全に外すことで、RS 未供給時の後方互換を保証（テストで現状一致を固定）。
- **指数と銘柄の営業日ズレ**: 指数 window の末尾と n 本前を使い、呼び出し側が「評価日以前」に切ることで look-ahead と日付整合を担保。データ不足は `None` フォールバック。
- **スケール定数の恣意性**: 名前付き定数に集約し、TDD では特定値でなく**符号・単調性・範囲・後方互換**という不変条件を検証。
- **backtest のコスト増**: RS は N 日リターン 2 本の割り算のみ（指標再計算なし）。各評価日 O(1) で無視可能。
- **look-ahead 混入**: 窓を切る責務を呼び出し側に統一（`regime_series` と同じ）。ヘルパは渡された window の末尾基準でのみ計算し、未来を参照しない。

## 8. 完了の定義

- backend テスト全グリーン（既存99件＋新規、ネット非依存）。
- `rs_strength=None` で score/direction/confidence が現状と一致（後方互換テストで保証）。
- RS 供給時、指数より強い buy 銘柄の confidence が上がり Top N 順位に反映される。
- backtest（`index_history` 供給／None 両方）が direction ベースの売買・PnL を変えない。
- final code-review 通過後、main へ FF マージ＋`git push origin main`。
