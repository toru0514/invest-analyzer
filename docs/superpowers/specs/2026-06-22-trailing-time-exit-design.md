# 打ち手9（前半）: トレーリングストップ＋時間切れ手仕舞い — 設計

- 日付: 2026-06-22
- 対応課題: 課題5「出口とサイジングが固定的」／打ち手9「トレーリング/時間切れ/分割利確を出口の選択肢に追加しバックテストで比較・R:Rを動的に」
- ブランチ: `move9-trailing-time-exit`
- 前提メモリ: [[roadmap-progress]]（打ち手8まで完了・フェーズC着手）

## 1. 背景・目的

現状の出口は **固定 ATR 倍**（`build_plan`: `stop = close − 1.5·ATR` / `target = close + 1.5·ATR`、R:R≈1:1）で一度きり算出する。バックテスト `_run_backtest_plan` は保有中に `low≤stop → "stop"` ＞ `high≥target → "target"` ＞ `direction=="sell" → "signal"` の優先で決済し、保有日数 `days = i−entry_i` を記録するが**出口判断には使っていない**。

課題5の核心は「スイングは入口より出口で決まる。伸びる時に伸ばし、ダメなら早く切る」。固定利確は伸びる波を取り逃し、停滞した建玉を切る仕組みも無い。本件は **トレーリングストップ**（伸ばす）と **時間切れ手仕舞い**（停滞を切る）を出口の選択肢として追加し、**バックテストで比較できる**状態を作る。

打ち手9 のうち **分割利確・R:R 動的化は本ブランチの対象外**（後続ブランチ）。理由は §9。

## 2. スコープ

**含む（本ブランチ）**
- トレーリングストップ（plan-mode バックテストの保有中、高値追随で stop を上にラチェット）。
- 時間切れ手仕舞い（保有 `max_hold_days` 到達で当日終値決済）。
- 上記を `/backtest`・`evaluation.evaluate_holdout` に貫通し、バックテスト出力で比較可能に。
- フロント: 既存バックテスト画面に任意入力2つ（トレーリング ATR 倍率・最大保有日数）。

**含まない（後続）**
- 分割利確（建玉分割決済 scale-out）。`build_plan` の複数 target・`daily_plan` スキーマ・フロント・部分決済まで波及が大きいため分離。
- R:R 動的化（レジーム/ボラで `target_mult` 可変）。`build_plan` の target 計算と作戦カードの見え方が変わるため分離。
- ライブ作戦ボード（`perform_refresh`／`daily_plan`／作戦カード）への出口採用。**有効性をバックテストで確認してから**（課題4・成功の定義の verify-before-adopt）。

## 3. 設計原則（打ち手6/7/8 と同一）

- **加法的・後方互換**: 整数 `score`/`direction`、`confidence`、サイジングは不変。新パラメータは**既定 OFF**（`trail_atr_mult=0.0`・`max_hold_days=0`）で**現挙動を完全再現**。既存 backend 122 テスト・frontend 16 テストは不変。
- **新 config ルールを足さない**: `DEFAULT_CONFIGS` は 14 個のまま（`test_api` の件数アサート不変）。出口パラメータは **`/backtest` リクエスト項目＋関数 kwarg** として供給する（stop_mult/target_mult のような config rule ではない）。理由: 「バックテストで比較」はリクエスト単位で別設定を回したいため、リクエスト引数が自然。
- **検証=提示の不破壊**: 既存の「作戦ボードと同一の提示指値で約定検証」は維持。本件は決済側の選択肢追加のみ。
- **look-ahead 安全**: 既存規約（各営業日はその日までのデータのみ／`i>entry_i` で決済判定）を厳守。

## 4. 出口セマンティクス（核心）

`_run_backtest_plan` は **買い専用**（pending=買い指値、保有=ロング）。保有中の各バー `i (>entry_i)` で次の優先順に判定する。

1. **トレーリング更新**（`trail_atr_mult>0` かつ `entry_atr` 有効なとき）
   `current_stop = trailing_stop(initial_stop, high_water_{≤i−1}, entry_atr, trail_atr_mult, "buy")`
   = `max(initial_stop, high_water_{≤i−1} − trail_atr_mult·entry_atr)`。**stop は上にのみラチェット**。トレーリング OFF のときは `current_stop = initial_stop`。
2. **日中の価格トリガ**
   - `low ≤ current_stop` → 決済。理由は **トレーリング ON なら "trail"・OFF なら "stop"**。約定 `current_stop` 価格（sell コスト適用）。
   - 上で未決済かつ **トレーリング OFF** のとき `high ≥ target` → 決済 "target"（既存どおり）。
     - **トレーリング ON 時は固定 target を無効化**（伸ばす意図。これが無いと近い固定 target が先に当たり比較にならない）。`target` 値自体は従来どおり pending/保有に保持するが出口には使わない。
3. **時間切れ**（`max_hold_days>0` かつ上で未決済）
   - `i − entry_i ≥ max_hold_days` → 当日 **終値** 決済 "time"（sell コスト適用）。
4. **signal**（既存・ブロック3）
   - 上の全ブロックで未決済かつ当日 `direction=="sell"` → 終値決済 "signal"。

**優先順のまとめ**: 日中価格トリガ（trail/stop・target）＞ 時間切れ（終値）＞ signal（終値）。同一バーで stop と時間切れが両立する場合は **日中 stop を優先**（実際にはザラ場で先に約定するため）。時間切れと signal が同一バーで両立する場合は **時間切れを優先**（ハードな保有上限）。

**high_water の更新（look-ahead 安全）**
- 約定（エントリー）時に `high_water = エントリーバーの high`、`entry_atr = pending["atr"]` を保持。
- バー `i` の `current_stop` は **`high_water`（`i−1` まで）** で算出し、**そのバーで未決済なら最後に `high_water = max(high_water, high_i)` で更新**する。当日高値が当日 stop に効かない（look-ahead 回避）。既存の「エントリー当日は決済しない（`i>entry_i`）」と整合。

**エッジ**
- pending が立つのは `build_plan` が limit/stop/target を返したときのみで、その場合 `plan["atr"]` は非 None（`build_plan` は atr None なら全 None 返し）。よって `entry_atr` は有効値。万一 `entry_atr` が無効でも `trailing_stop` は `initial_stop` を返し（§5）、トレーリングは無効化されるが target も無効化されたままになるため、**`trail_atr_mult>0` でも `entry_atr` 無効なら固定 stop＋固定 target にフォールバック**（trailing を実質 OFF 扱い）する条件で実装する。
- gap 貫通（寄りで stop を飛び越える）は**モデルしない**＝従来どおり stop/target/trail 価格ぴったりで約定（gap は課題7の領分）。

## 5. 純関数 `signals.trailing_stop`

```python
def trailing_stop(initial_stop, extreme, atr, mult, direction="buy") -> float:
    """トレーリングストップ価格を返す純関数。

    ロング(direction="buy"): max(initial_stop, extreme − mult·atr)（下限を上にのみ引き上げ）。
    ショート(direction="sell"): min(initial_stop, extreme + mult·atr)（上限を下にのみ引き下げ）。
    extreme は建玉開始以降の最有利値（ロング=最高値, ショート=最安値）。

    mult≤0 / atr≤0 / extreme・initial_stop が None・非数 のときは initial_stop をそのまま返す
    （トレーリング無効・例外は投げない契約。position_size と同じ堅牢方針）。
    """
```

- **方向対応**の理由: `build_plan` が buy/sell 両対応であることとの対称性＋将来のショート側バックテスト。現 plan-mode バックテストは**ロング経路のみ使用**するが、ショート経路も単体テストで固定する（テスト済みであり死コードではない）。
- `float()` 一括キャスト＋ガードで None/文字列/nan/非正を吸収（`position_size` の契約に倣う）。

## 6. `backend/backtest.py` の変更

### `_run_backtest_plan`
- シグネチャに `trail_atr_mult: float = 0.0, max_hold_days: int = 0` を追加。
- ループ変数に `entry_atr = None`、`high_water = None` を追加（`entry_price = stop = target = None` と並べる）。
- 約定ブロック: `pending` に `"atr"` を含めて立て、約定時に `entry_atr = pending["atr"]`、`high_water = high` を保持。
- 保有中決済ブロックを §4 の優先順で書き換え（trail/stop・target・time）。`high_water` を未決済時に更新。
- 決済時のリセットに `entry_atr = None; high_water = None` を追加。
- 戻り値に **`trail_exit_count`**・**`time_exit_count`** を追加（既存 `take_profit_count`/`stop_loss_count`/`signal_exit_count` と同型 `sum(... reason==...)`）。`exit_mode` は "plan" のまま。
- 発注ブロックの `pending` 生成に `"atr": plan["atr"]` を追加。

### `run_backtest`
- シグネチャに `trail_atr_mult=0.0, max_hold_days=0` を追加し、plan ディスパッチ（`exit_mode in ("plan","atr")`）で `_run_backtest_plan(..., trail_atr_mult=trail_atr_mult, max_hold_days=max_hold_days)` を貫通。**score モードは無視**（時間切れ/トレーリングは plan-mode 限定）。

## 7. `backend/evaluation.py` の配線

- `evaluate_holdout(..., trail_atr_mult=0.0, max_hold_days=0)` を追加し、内部の plan-mode バックテスト呼び出し（`_bt`）に貫通（打ち手7/8 の `risk_pct`/`rs_params` 貫通と同様の経路一貫性）。**既定 OFF**。
- `benchmark` は score モードのため対象外（貫通しない）。
- グリッド探索（閾値スイープ）に出口パラメータは**加えない**（過学習回避・課題4）。固定の出口設定を OOS 評価できるようにする passthrough のみ。

## 8. `backend/main.py` / API の配線

**重要（リスクサイジングとの対比）**: 既存の `risk_pct` は永続設定（`app_meta`）から `_risk_pct()` 経由で供給される**ユーザーの安定した選好**であり、`BacktestIn`/`OptimizeIn` のペイロード項目では**ない**。一方 `trail_atr_mult`/`max_hold_days` は**リクエストごとに出口設定を変えて比較する実験パラメータ**なので、**設定ではなく `BacktestIn`/`OptimizeIn` のペイロード項目**として供給する（＝`risk_pct` の供給経路は踏襲しない。§3 の「リクエスト引数が自然」と一致）。`evaluate_holdout`/`run_backtest` への kwarg 貫通（§6・§7）は `risk_pct` と同型でよいが、**入口（API 層）が設定でなくペイロードである点が異なる**。

- `BacktestIn` に `trail_atr_mult: float = 0.0`・`max_hold_days: int = 0` を**新規ペイロード項目**として追加。**検証は ge=0**（pydantic `Field(..., ge=0)`、負値・不正は弾く or 0 にフォールバック）。`max_hold_days` は整数。
- `/backtest` で `run_backtest(..., trail_atr_mult=payload.trail_atr_mult, max_hold_days=payload.max_hold_days)` を渡す。
- `/optimize`（`OptimizeIn`/`evaluate_holdout`）にも同項目を追加し passthrough（既定 OFF）。経路一貫性のため。
- `perform_refresh`・`build_plan`・`daily_plan`・`upsert_plan` は**変更しない**（ライブ作戦は現挙動維持）。

## 9. フロント（最小）

- 既存バックテスト画面に**任意入力2つ**（「トレーリング ATR 倍率」「最大保有日数」、空欄＝OFF＝0）を追加し、`/backtest` ペイロードに貫通。
- 対象ファイル（spec-reviewer 確認済み）:
  - `frontend/src/app/simulation/page.tsx`（`useState` の state・ペイロード構築は行31-33 付近）に入力2つを追加。
  - `frontend/src/lib/api.ts`: `api.backtest` の body 型（行197 付近）に `trail_atr_mult?`/`max_hold_days?` を追加。`BacktestResult` 型（行104-135）に `trail_exit_count`/`time_exit_count` を追加して新カウントを型で受ける。
- 比較表 UI（複数出口設定を並べて表示）は**後続**（必要なら案C の比較専用エンドポイントとセットで）。
- 純関数でペイロード組み立てがあるならそこを単体テスト。既存 16 テストは不変、新規テストを追加。

## 10. バックテスト出力の追加フィールド

`_run_backtest_plan` 戻り値（plan モード）に追加:
- `trail_exit_count`: トレーリング決済数（理由 "trail"）。
- `time_exit_count`: 時間切れ決済数（理由 "time"）。

既存の `take_profit_count`/`stop_loss_count`/`signal_exit_count`/`avg_holding_days`/`risk_reward`/`win_rate`/`pnl_pct` 等と合わせ、**出口設定を変えた複数ランの比較**に用いる（課題9「バックテストで比較」の具体的達成手段）。

**カウントの排他性（読み方の注意）**: §4 の通り、トレーリング ON のランでは保護的決済（初期 stop ヒットを含む `low≤current_stop` 全て）が `"trail"` に計上されるため `stop_loss_count` は 0 になり、固定 target 無効化により `take_profit_count`（"target"）も 0 になる。すなわち `trail_exit_count` と `stop_loss_count`/`take_profit_count` は**モードごとに排他**（trailing OFF なら trail=0／trailing ON なら stop=target=0）。比較時はこの対応を踏まえて読む。

## 11. 後方互換・回帰

- `trail_atr_mult=0.0` かつ `max_hold_days=0`（全既定）で **決済ロジックは現行と同一パス**を通る（trail/time ブロックは条件で素通り、target は有効）。
- 回帰テストで「既定 OFF のとき従来と同一の `closed_trades`・約定価格列・`closed_pnls`」を固定する。
- 新 config ルールなし＝`DEFAULT_CONFIGS` 14・`test_api` 件数不変。フロント変更は加点のみで既存不変。

## 12. テスト計画（TDD）

- `test_signals.py`
  - `trailing_stop`: ロングで上にのみラチェット（`extreme` 上昇で stop 上昇・下降では据え置き）、`mult=0`/`atr=0`/None で `initial_stop` 返し、ショート方向で下にのみラチェット。
- `test_backtest.py`
  - トレーリング: 決定論的な上昇後の押し目で、固定 target を超えて伸びた後トレーリング stop が利益を確保して決済（理由 "trail"・pnl>0）。同データを **trail OFF（固定 target）と比較**して挙動差を固定。
  - 時間切れ: stop/target に当たらず停滞する建玉が `max_hold_days` で終値決済（理由 "time"・`days==max_hold_days`）。
  - 既定 OFF 回帰: 既存挙動と同一（`closed_pnls`・約定価格・各 count）。
  - look-ahead: 当日高値が当日トレーリング stop に効かない（前バーまでの high_water で判定）ことを固定。
- `test_evaluation.py`
  - `evaluate_holdout` が新パラメータ受領で完走するスモーク（挙動差は backtest 層で担保。`test_evaluate_holdout_accepts_risk_pct` に倣う）。
- `test_api.py`
  - `/backtest` が `trail_atr_mult`/`max_hold_days` を受領し、未指定で挙動不変。`DEFAULT_CONFIGS` 件数不変。
- フロント（vitest）
  - 入力 → `/backtest` ペイロード貫通（空欄=OFF）。

各テストは [[signal-tests-prefer-deterministic-ohlc]] に従い**統制 OHLC で決定論化**する（seed 走査しない）。バックテストのトレーリング/時間切れ検証は OHLC を直接組み立て、トリガ条件を厳密に満たすデータを用いる。

## 13. 非スコープ・将来拡張

- 分割利確（scale-out）／R:R 動的化（打ち手9 後半）。
- ライブ作戦ボードへの出口採用（backtest で有効性確認後）。
- トレーリングの ATR をローリング ATR にする（現状はエントリー時 ATR 固定で決定論・単純。Chandelier 型は将来）。
- 比較専用エンドポイント＋比較表 UI（案C）。
- ショート側 plan-mode バックテスト（`trailing_stop` は既に方向対応）。

## 14. リスク・割り切り（v1）

- gap 貫通を約定価格に反映しない（課題7 で対応）。
- 発注済み pending は翌日地合い悪化でも約定し得る（既存モデル準拠）。
- トレーリング ON で固定 target を無効化する設計判断（伸ばす目的・比較の意味を出すため）。固定 target との併用（giveback 抑制型）は将来の別オプション。
