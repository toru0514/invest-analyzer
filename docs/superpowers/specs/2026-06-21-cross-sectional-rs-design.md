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

**ヘルパ `relative_strength(ticker_df, index_df, n, scale, asof=None)`**:
- 戻り値 `float | None`。データ不足（どちらかが `n+1` 本未満）や `close[t-n] <= 0` → `None`（RS 無効＝後方互換でその銘柄は従来確信度）。
- **look-ahead の担保（重要・既存パターンとの正確な対応）**: 既存の `regime_series` は窓を切らず、全期間で日次レジームを **precompute** し、`backtest.py:_regime_at` が `regime_series.asof(d)` で「d 以前の最も近い値」を引く方式（窓スライスではない）。RS も**この `.asof(d)` 流儀に揃える**:
  - ヘルパは `asof`（評価日 = pandas Timestamp）を受け、`ticker_df` と `index_df` の双方を **`asof` 以前**に絞ってから（`df[df.index <= asof]`）末尾と n 本前を取る。`asof=None` のときは各 df の末尾（最新）を基準日とする（refresh 用）。
  - これにより「位置インデックス（`_run_backtest_plan` の `df.iloc[:i+1]`）」と「日付インデックス（`index_df`）」の混在を**ヘルパ内に閉じ込め**、呼び出し側は評価日 `asof = df.index[i]`（plan 経路）／`asof = d`（run_backtest 経路）／`asof=None`（refresh）を渡すだけにする。
  - 指数と銘柄の営業日ズレ（指数が当日未更新等）は、双方を同じ `asof` 以前に切ることで吸収（銘柄末尾日 > 指数末尾日でも、両者それぞれの「`asof` 以前の末尾」を基準にする）。
- 純粋関数（ネット非依存）。`market.py` ではなく `signals.py` に置く（指標系と同居）。
- backtest 2経路で同型にするため、`backtest.py` 側に `_regime_at` と並ぶ薄いラッパ `_rs_at(index_history, n, scale, d)` を置き、`relative_strength(window, index_history, n, scale, asof=d)` を呼ぶ（`window` は当該銘柄の評価窓）。

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
   - 供給時のみ5グループ目として正規化に参加。固定分母方針（証拠が薄いほど保守的）は維持。
   - **非単調性の明示（意図的挙動）**: 分母が増えるため、「正の RS を足せば必ず confidence が上がる」とは限らない。`_strength_net = Σ w_g·s_g / Σ w_g·GROUP_CAP` なので、`s_rs` が**既存グループの加重平均強度を下回る**と正でも `_strength_net` は希釈されて下がる。これは「相対力が市場並み（弱い）なら確信度を割り引く」という妥当な設計であり、テストでは「`s_rs` が既存平均強度を上回るとき上がる／下回るとき下がる」という**条件付き単調性**で検証する（§6）。素朴な「正なら上がる」では反例が出る点に注意。
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

RS は `regime_series` が通っているのと**同じ全経路**に貫通させる（「`regime_series` が通る箇所＝`index_history` も通る」と1対1に対応付けると漏れない）。RS 計算で使う `period`/`scale` は common の `relative_strength` 設定から引き、各経路の入口で1度だけ取り出して引数で配る。

**perform_refresh**（`main.py:340-374`）:
- 既に `idx_df = get_history(index_ticker, ...)` を取得済み（L356）。`_find_cfg(common, "relative_strength")` で RS 設定を引く（`None` なら RS 無効）。
- 各銘柄ループ内で `rs = relative_strength(df, idx_df, n, scale, asof=df.index[-1])` を計算し `evaluate(..., rs_strength=rs)` へ。`asof=df.index[-1]` で**指数を銘柄最終日以前に揃える**（指数が当日未更新でも整合）。
- `idx_df` 取得失敗・設定不在時は `rs=None`（best-effort・従来確信度）。

**run_backtest**（`backtest.py:28, 62-67`・日付外側ループ）:
- `run_backtest(..., index_history=None, rs_params=None)` を任意引数で追加（`rs_params` = `{"period","scale"}` または `None`）。各評価日 `d` で `_rs_at(index_history, n, scale, d)` 相当を使い `rs_strength=relative_strength(window, index_history, n, scale, asof=d)` を `evaluate` へ。末尾再判定（L106-110）にも同様に供給。
- `index_history is None` または `rs_params is None` → `rs_strength=None`（従来どおり）。

**_run_backtest_plan**（`backtest.py:134, 200-203, 228-229`・銘柄外側ループ）:
- 同様に `index_history`/`rs_params` を受け、`window = df.iloc[:i+1]` に対し `asof=df.index[i]` を渡して RS を算出（位置→日付の変換はヘルパの `asof` に閉じ込め）。末尾再判定（L228-229）にも供給。

**evaluation.py の貫通（影響大・spec 初版で欠落していた経路）**:
- `/optimize` は `evaluate_holdout`（`main.py:543`）、`/backtest` は `benchmark`（`main.py:508`）を経由し、**いずれも内部で `run_backtest` を複数回呼ぶ**（`evaluation.py:59, 95`）。`regime_series` がこの両関数を貫通しているのと同様に、`index_history`/`rs_params` も貫通させる必要がある。
- `benchmark(..., regime_series=None, index_history=None, rs_params=None)` を追加し、内部 `run_backtest`（L59）へ素通し。
- `evaluate_holdout(..., regime_series=None, index_history=None, rs_params=None)` を追加し、内部クロージャ `_bt`（L94-98）と全 `run_backtest` 呼び出し（train/leave-one-out/oos）へ素通し。

**RS 設定の供給**: `_fetch_regime_series`（`main.py:51-58`）が既に index を取得しているのと同様に、backtest 呼び出し箇所（`/optimize` L499-510・`/backtest` L543-545）で index history（既存の `_fetch_regime_series` 用 idx を再利用可）と `rs_params` を取得して `benchmark`/`evaluate_holdout`/`run_backtest` に渡す。取得失敗・設定不在時は `None` で RS 無効（後方互換）。

### 4.5 設定

`DEFAULT_CONFIGS`（`signals.py:26`）に common ルールを追加（既存 `market_regime` と同じ流儀）:

```python
{"rule_type": "relative_strength",
 "params": {"period": 20, "scale": 0.10, "enabled": 1},
 "weight": 1, "enabled": 1},
```

- `period`（N 日・既定 20）・`scale`（tanh 正規化幅・既定 0.10）。
- 呼び出し側は `_find_cfg(common, "relative_strength")` で params を引く。**`_find_cfg` は `enabled` を内部で見て（`signals.py:126` の `c.get("enabled", 1)`）無効ルールには `None` を返す**ので、`None` が返れば RS 無効（`rs_strength=None`）、非 `None` なら params から `period`/`scale` を取得、という `volume_filter`/`market_regime` と完全に同じパターンにする（spec 内で `enabled` を二重に見ない）。

## 5. 影響ファイル

- `backend/signals.py`:
  - `relative_strength(ticker_df, index_df, n, scale)` ヘルパ新規。
  - `RS_STRENGTH_SCALE` 定数、`REGIME_GROUP_WEIGHTS` に `rs` 追加。
  - `_score_indicators` に `rs_strength` 引数＋`_CONF_GROUPS` 動的化＋`detail["rs"]`。
  - `evaluate` に `rs_strength` 引数（_score_indicators へ素通し）。
  - `DEFAULT_CONFIGS` に `relative_strength` ルール。
- `backend/main.py`: `perform_refresh` で RS 計算・evaluate へ供給。`/optimize`・`/backtest` で index history＋`rs_params` を取得し `evaluate_holdout`/`benchmark` へ渡す。
- `backend/backtest.py`: `run_backtest`・`_run_backtest_plan` に `index_history`/`rs_params` 任意引数、`_rs_at` ラッパ、各評価日で RS を算出して evaluate へ。
- `backend/evaluation.py`: `benchmark`・`evaluate_holdout` に `index_history`/`rs_params` 任意引数を追加し、内部の全 `run_backtest` 呼び出し（`_bt` クロージャ含む）へ素通し（`regime_series` と同じ貫通）。
- テスト: `backend/test_signals.py`（RS 強度の符号・単調性・None 後方互換・確信度への寄与）、`backend/test_backtest.py`（index_history 供給時／None 時の不変性）、必要なら `backend/test_api.py`（refresh で RS が確信度に反映され例外なく動く）。
- フロント: 新規コンポーネントなし。確信度バッジ・Top N は既存のまま（RS は confidence 数値として反映）。`detail.rs` を表示に出すかは任意（v1 は出さなくてよい）。

## 6. テスト戦略（TDD・ネット非依存）

決定論データ（`_idx`/`_declining_df` ＝ [[signal-tests-prefer-deterministic-ohlc]]）で性質を検証する。

- **`relative_strength` の符号**: 銘柄が指数より強く上昇（excess>0）→ `s_rs>0`。逆 → `s_rs<0`。同一推移 → ≈0。
- **単調性**: excess が大きいほど `|s_rs|` が大（tanh の単調性）。
- **データ不足/ゼロ除算**: `n+1` 本未満・`close[t-n]<=0` → `None`。
- **後方互換（最重要）**: `rs_strength=None`（既定）で `evaluate` の `score`・`direction`・`detail["confidence"]` が**現状と完全一致**（既存テスト・既存確信度数値が不変）。`isinstance(score, int)` 維持。
- **確信度への寄与（条件付き単調性）**: buy 方向の銘柄で、`s_rs` が既存グループの加重平均強度を**上回る**と `confidence` が上がり、**下回る**と下がる（§4.2 の非単調性を踏まえ「正なら必ず上がる」ではなくこの条件で検証）。`0 ≤ confidence ≤ 100` を常に保証。
- **レジーム重み**: `risk_on` で rs の確信度寄与が `neutral`/`risk_off` より大（rs 重み 2 vs 1）。
- **5グループ正規化**: rs 供給時に `_strength_net` の分母が5グループ分になり、他グループが同じでも値が変わる（正規化が効いている）こと。
- **backtest 不変性（score モード）**: `index_history=None`／`rs_params=None` で `run_backtest` の結果が現状と一致。供給時も **closed PnL・direction ベースの売買は不変**（RS は score/direction を動かさないため）。
- **backtest 不変性（plan モード）**: `_run_backtest_plan` でも `index_history` 供給有無で trades/closed が一致することを明示テスト。これは `build_plan`（`signals.py:576`〜）が confidence/`detail["rs"]` を**参照しない**ことが前提なので、その前提も不変条件として固定する（将来 build_plan が confidence を見たら検知できるように）。
- **look-ahead 安全**: ヘルパに `asof` を渡したとき、`asof` 以降のデータを足しても RS 値が変わらない（過去のみ参照）ことを決定論データで検証。
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
