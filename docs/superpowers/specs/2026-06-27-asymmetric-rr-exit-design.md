# 打ち手9後半（出口R:R）: 非対称R:R出口のライブ採用 — 設計

- 日付: 2026-06-27
- 対応課題: 課題5「出口とサイジングが固定的」／打ち手9「出口を比較・R:Rを動的に」。本ブランチは**診断で確定した出口R:Rの是正**。
- ブランチ: `feat/asymmetric-rr-exit`
- 前提メモリ: [[roadmap-progress]]

## 1. 背景・目的（診断の結論）

実データ3年・5銘柄の verify-before-adopt 診断で、**素のシグナル戦略が上げ相場（buy&hold +118.8%）で −17.1% と負ける**ことが判明。根本原因を systematic-debugging で特定:

- **固定ATR利確が対称 R:R≈1:1（target=終値+1.5·ATR / stop=終値−1.5·ATR）で、シグナルの「少数の大勝ち・多数の小負け」モメンタム型ペイオフと噛み合わない。** 同一シグナルでも反転まで保有する score モードは +38.3%（avgWin 37,843）。plan モードは1.5ATR利確で勝ちを6,038に切り、負けは同等のまま → R:R が1.3に潰れ構造的に負ける。
- **clean A/B（利確倍率以外を固定したスイープ）で確証**: target_mult 1.5→8.0 で pnl −17→+4・avgWin 4倍・DD半減と単調改善。交絡なし。
- 棄却: シグナル方向（勝率はモード不問で約31-33%）・指標構成（ablation ±2.7%）・銘柄集中（全5銘柄同形）・閾値。

**本件は、検証で頑健だった非対称R:R（勝ちを伸ばし負けは1.5ATRで切る）を build_plan の既定にし、作戦カードとバックテスト双方に反映する。**

検証（出口候補 tune・stop1.5固定・full / OOS）:
| 出口 | full pnl% / DD% | OOS pnl% / 期待値 / n |
|---|---|---|
| baseline tgt1.5（R:R1:1） | −16.8 / 19.8 | −4.0 / −633 / 61 |
| **tgt6（R:R4:1・本採用）** | **−0.5 / 11.0** | **−0.5 / −175 / 46** |
| 固定利確撤廃 tgt30 | +24.3 / 9.1 | +6.5 / −1551 / **28**（n<30・高分散で非採用） |
| trail 2.5（ステートフル） | +0.7 / 9.4 | +1.3 / +211 / 45（次ブランチ） |

→ **提示可能な固定R:Rで両窓が改善する頑健な底が tgt6（R:R4:1）**。固定利確撤廃は full 最良だが OOS n<30・期待値マイナスで過学習リスク、トレーリングはステートフルゆえ別ブランチ。

## 2. スコープ

**含む（本ブランチ）**
- `atr_exit` config の既定を **`stop_mult=1.5` / `target_mult=6.0`（R:R 4:1）** に（現状 target_mult=1.5）。
- 既存DB（`data.db`）の `atr_exit` config を**非クロバー移行**（target_mult が旧既定1.5のときだけ6.0へ）。
- 上記により build_plan の利確が遠くなり、**作戦カード（利確目安）とバックテスト（提示指値で検証）が自動的に新R:Rで動く＝検証=提示を維持**。

**含まない（YAGNI・後続）**
- **トレーリングのライブ採用（打ち手9後半B）**。ステートフル（建玉後高値の追跡）・保有者限定・カードUX変更が必要。診断で最頑健（OOS期待値プラス）だが独立ブランチ。`signals.trailing_stop` は実装済み・backtest 採用済み。
- **R:R動的化**（レジーム/ボラ別 target_mult）。build_plan にレジーム入力が要る。静的非対称で edge の大半を回収できるため後送り。
- **分割利確（scale-out）**・**入口の取り逃し是正（fill 32%）**。別レバー・別ブランチ。

## 3. 設計原則

- **検証=提示の不破壊**: build_plan の提示指値（stop/target）でバックテスト約定する既存構造を維持。本件は build_plan が読む `target_mult` の**既定値変更のみ**で、コード経路は不変。
- **加法的・最小**: build_plan / backtest / カードの**コードは無改変**（target_mult を config から読む既存経路がそのまま新値で動く）。新 config ルールを足さない＝`DEFAULT_CONFIGS` 14個・`test_api` 件数不変。
- **config 可変・再検証前提**: R:R は `atr_exit` の params で従来どおり編集可能。経験的基礎は5銘柄/3y＝方向性。**銘柄を増やして再検証する**前提（後述リスク）。
- **非クロバー移行**: ユーザーが意図的に設定した値は壊さない（旧既定1.5のときだけ更新）。

## 4. 変更内容

### 4.1 `backend/signals.py`（既定値）
`DEFAULT_CONFIGS` の `atr_exit`（現 `signals.py:49`）の `params.target_mult` を **1.5 → 6.0** に変更。`stop_mult` は 1.5 のまま。他パラメータ（length/limit_method/limit_ma/entry_atr_mult/support_n）不変。
→ 新規DB（`init_db` シード）と、明示 config を渡さない build_plan 呼び出し（`build_plan(df, dir, score)` の DEFAULT_CONFIGS 経路）が新R:Rになる。

### 4.2 `backend/db.py`（既存DB移行）
`init_db`（`db.py:150`）に冪等移行 `_migrate_exit_rr(conn)` を追加（`_migrate_daily_plan` と同様に呼ぶ）:
- `signal_config` の `rule_type='atr_exit'` 全行（common＝ticker NULL ＋ per-ticker）について params(JSON) を読み、**`target_mult == 1.5` の行だけ** `target_mult=6.0` に更新（`json.dumps` で書き戻し）。
- 冪等（6.0 のときは何もしない）・非クロバー（1.5以外は触らない）。
- 既存の「不足 rule_type を追加」シード（`db.py:180-185`）は params を更新しないため、この移行で既存 atr_exit の値を是正する。

### 4.3 build_plan / backtest / カード
**コード変更なし**。build_plan は `_find_cfg(configs,"atr_exit").get("target_mult", 1.5)` で読む（`signals.py:631`）ため、config 値が変われば buy/sell/neutral の target_price が自動的に遠くなる。バックテストは build_plan の提示で約定するため新R:Rで検証される。作戦カードは `target_price` を表示するだけ（`plan/page.tsx`）。

## 5. テスト計画（TDD）

- `test_signals.py`
  - 新規 `test_build_plan_default_rr_is_asymmetric`: 明示 config 無しの `build_plan(df,"buy",3)` で **(target−close) / (close−stop) ≈ 4.0**（target_mult/stop_mult＝6/1.5）を固定。sell 側も R:R 4:1 を確認。
  - 既存 `test_build_plan_exits_are_ordered`（順序のみ・R:R値非依存）は不変で通る（target が遠くなるだけ）。
  - 既存 `test_build_plan_limit_method_switch`（明示 config・target_mult 1.5）は DEFAULT 非依存で不変。
- `test_db.py`（無ければ新規 or `test_api.py`）
  - `_migrate_exit_rr`: 旧スキーマ風 DB に target_mult=1.5 の atr_exit を入れ、`init_db` 後に 6.0 になる。**ユーザー設定値（例3.0）は不変**（非クロバー）。冪等（再実行で不変）。`test_daily_plan_*_migration` と同じ `tmp_path`＋`monkeypatch.setattr(db,"DB_PATH",...)` 方式。
- `test_api.py`
  - `DEFAULT_CONFIGS` 件数14不変（params 値変更のみ）。
- **回帰**: backtest の既定OFF回帰・RS不変等は base vs explicit の**相対比較**で、両方が新 target_mult を使うため等価維持。ただし **DEFAULT_CONFIGS を使い絶対値をアサートするテストがあれば更新**（実装時に全スイート実行＝`backend/venv/bin/python -m pytest backend/ -q` で確認し、target_mult 既定変更由来の破綻を是正）。

## 6. 後方互換・回帰

- 整数 `score`/`direction`・`confidence`・サイジング・trail/time/earnings の経路は不変。
- 新 config ルールなし＝件数14不変。スキーマ変更なし（params 値のみ移行）。
- フロント無改変（カードは target_price をそのまま表示）。既存フロントテスト不変。
- 移行は既存DBの atr_exit を旧既定→新既定に是正するのみ（schema 不変）。

## 7. 非スコープ・将来拡張

- トレーリングのライブ採用（打ち手9後半B・ステートフル保有者stop助言・OOS期待値プラスの本命）。
- R:R動的化（レジーム/ボラ別 target）。
- 分割利確（scale-out）／入口の取り逃し是正（push-limit の fill 32%・上限を上げる別レバー）。
- 出口R:Rを設定画面に出す（現状は config 編集経路で可変）。

## 8. リスク・割り切り（v1）

- **経験的基礎は5銘柄/3y＝方向性**。target_mult=6.0 は OOS n=46 で ≈±0 と検証したが、**銘柄を増やして再検証する**（config 可変なので変更容易）。
- **移行のクロバー懸念**: ユーザーが意図的に target_mult=1.5 にしていた場合も新既定へ動く（1.5＝旧既定と区別不能のため）。1.5 を望むなら config で再設定可能。
- **到達点は≈±0**: 「明確な負け→低DDで≈±0」への前進であり、buy&hold +33.8% には届かない（入口の取り逃しが別ボトルネック＝将来）。本件は出口がボトルネックだった事実の是正。
- DEFAULT 値変更がバックテスト系テストの絶対値アサートに波及し得る（実装時に全スイートで是正）。
