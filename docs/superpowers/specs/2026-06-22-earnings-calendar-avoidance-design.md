# 打ち手10: 決算日カレンダー＋決算跨ぎ回避 — 設計

- 日付: 2026-06-22
- 対応課題: 課題7「イベント（決算・権利落ち）リスクを無視している」／打ち手10「決算日を取得し、作戦に『N日後に決算』警告と決算跨ぎ回避オプション、ギャップリスクをバックテストに反映」
- ブランチ: `feat/earnings-calendar-avoidance`
- 前提メモリ: [[roadmap-progress]]（打ち手9前半まで完了・フェーズC継続）

## 1. 背景・目的

スイングの最大の事故は**決算ギャップ**。終値ベースの指値・損切りは寄りで窓を開けると機能せず、損切りを軽く飛び越える（課題7）。現状アプリは決算日カレンダーを一切持たず、`market.fetch_earnings_days(ticker)→int|None`（次回決算までの日数・best-effort）が `perform_refresh` で AI解説のコンテキストに渡されるだけ（`main.py:436,443`）で、**作戦カードにも daily_plan にも永続化されず**、バックテストもギャップを無視している（打ち手9 spec §14「gap 貫通は課題7の領分」）。

本件は課題7 の3点を実装する:
1. **決算日取得**（既存 `fetch_earnings_days` ＋新規 `fetch_earnings_dates`）。
2. **作戦カードへの「N日後に決算」警告**（daily_plan に永続化＋カード表示）。
3. **バックテストへのギャップ反映＋決算跨ぎ回避オプション**（plan モードで、決算翌日の窓を寄りfillで再現し、跨ぎ回避との比較を可能に）。

## 2. スコープ

**含む（本ブランチ）**
- `market.fetch_earnings_dates(ticker, limit=12)→list[Timestamp]|None`（過去＋将来の決算日付列・正規化）。
- ライブ作戦: `daily_plan.days_to_earnings` 列追加・`perform_refresh` で永続化・作戦カードに警告バッジ＋助言（**direction を問わず**表示・display 専用）。
- バックテスト（plan モードのみ）: PAYLOAD パラメータ `earnings_aware`（決算翌日の窓を寄りfillで再現）＋`earnings_exit_days`（N営業日前に手仕舞い＝跨ぎ回避）。`/backtest`・`/optimize`(`evaluate_holdout`) に貫通。
- フロント: 作戦カードの警告表示＋バックテスト画面の任意入力2つ＋内訳カウント2つ。

**含まない（YAGNI・後続）**
- **手入力の決算日テーブル＋CRUD＋UI**。日本株の yfinance 欠落対策として価値はあるが、別テーブル・エンドポイント・設定画面が必要で独立性が高い。v1 は yfinance best-effort のみ。`fetch_earnings_dates` を唯一のデータ源にし、将来の手入力はこの関数の置換/合流点として残す。
- **権利付/落ち日・分割カレンダー**（課題7 が併記するが、決算ギャップが最優先かつ取得経路が別。別途）。
- **ライブ作戦のエントリーのハードゲート化**（決算前は買い提案を出さない等）。打ち手9 と同じ verify-before-adopt 方針で、まず警告（提示）に留め、跨ぎ回避の有効性をバックテストで確認してから採用判断する。
- 決算サプライズ・短信内容の定性取り込み（＝打ち手14・フェーズE）。

## 3. 設計原則（打ち手6/7/8/9 と同一）

- **加法的・後方互換**: 整数 `score`/`direction`、`confidence`、サイジング、既存の出口（trail/time）は不変。新パラメータは**既定 OFF**（`earnings_aware=False`・`earnings_exit_days=0`）で**現挙動を完全再現**（バイト一致）。
- **新 config ルールを足さない**: `DEFAULT_CONFIGS` は 14 個のまま（`test_api` の件数アサート不変）。決算パラメータは打ち手9 と同じく **`BacktestIn`/`OptimizeIn` のペイロード項目＋関数 kwarg** として供給する（リクエストごとに跨ぎ回避を ON/OFF して比較する実験パラメータ。永続設定 `risk_pct` とは入口が異なる）。
- **決算ロジックは `_run_backtest_plan`（plan モード）に閉じ込める**。score モードの benchmark（buy&hold／全シグナル）は対象外＝終値ベースで窓を自然に内包するため特別扱い不要。波及を最小化する。
- **look-ahead 安全**: 決算"日"はスケジュールとして事前公表される（＝判定時に既知で正当。決算の"結果"は一切使わない）。ギャップの fill は当日バーの寄り(open)のみを使い未来値を参照しない。
- **ライブは display 専用**: `daily_plan.days_to_earnings` は生の日数を保存し、警告するかの**しきい値判定はフロント**（再 refresh 不要でしきい値調整可）。`build_plan`/score/sizing/direction は不変。

## 4. ① 決算日取得（`backend/market.py`）

既存 `fetch_earnings_days(ticker)→int|None`（次回**将来**決算までの**暦日**数・`main.py:436` で使用）は**ライブ警告にそのまま流用**（変更しない）。

新規:

```python
def fetch_earnings_dates(ticker: str, limit: int = 12) -> list[pd.Timestamp] | None:
    """過去＋将来の決算日（正規化済み・昇順）。取得不可・無しは None（best-effort）。

    yfinance Ticker.get_earnings_dates の index（tz-aware なことが多い）を
    tz-naive・midnight に正規化して返す。例外・空は None（fetch_earnings_days と同契約）。
    """
```

- yfinance `Ticker(ticker).get_earnings_dates(limit=limit)` の index を `pd.to_datetime(...).tz_localize(None).normalize()`（tz があれば落とす）で**tz-naive・日付**化し、ソートして返す。バックテストの df index（`get_history` 由来＝tz-naive）と素直に比較できるようにする。
- 例外・None・空 → `None`（`fetch_earnings_days` と同じ堅牢契約・例外を投げない）。
- **demo・テストでは呼ばない**（ネット非依存を維持）。バックテストには `earnings_map`（後述）として注入し、単体テストは合成 `edates` を直接渡す。

## 5. ② ライブ作戦への決算警告

### 5.1 永続化（`backend/db.py`）
- `daily_plan` に列 **`days_to_earnings INTEGER`** を追加:
  - `SCHEMA` の `CREATE TABLE daily_plan` に1列追加。
  - `_migrate_daily_plan`（`db.py:138`）の後付け列タプルに `("days_to_earnings", "INTEGER")` を追加（冪等・confidence/shares と同型）。
  - `upsert_plan`（`db.py:413`）の INSERT 列・VALUES・ON CONFLICT DO UPDATE・先頭の `setdefault` ループ（`db.py:416`）に `days_to_earnings` を追加。
- **マイグレーションのタプルに足すのは実際に後付けの列だけ**（roadmap の打ち手8 注意点を踏襲）。

### 5.2 `perform_refresh`（`backend/main.py`）
- `days_to_earnings` は既に `main.py:436` で計算済み（`None if demo else fetch_earnings_days(ticker)`）。これを `upsert_plan({...})`（`main.py:447`）の dict に **`"days_to_earnings": days_to_earnings`** として追加するのみ。AI解説への受け渡し（`main.py:443`）は不変。
- demo は None（ネット非依存）。非buy（neutral/sell）でも保有者向けに保存する（カードで direction 非依存に警告するため）。

### 5.3 作戦カード（フロント）
- `api.ts` `PlanRow` 型（`api.ts:68-85`）に `days_to_earnings: number | null` を追加。
- `rows.ts` に純関数 `earningsWarning`:
  ```ts
  export const EARNINGS_WARN_DAYS = 5;   // この暦日数以内なら警告
  export function earningsWarning(daysToEarnings: number | null,
                                  threshold = EARNINGS_WARN_DAYS): { days: number } | null;
  ```
  `daysToEarnings == null || daysToEarnings < 0 || daysToEarnings > threshold` のとき `null`、それ以外 `{ days }`。純粋・テスト対象。
  - **単位の注意**: ライブ警告の `EARNINGS_WARN_DAYS`（5）は**暦日**（`fetch_earnings_days` が暦日を返すため）。バックテストの `earnings_exit_days`（§6・**取引バー**基準）とは**わざと別単位**＝UI 文言で両者を混同しない。
- `plan/page.tsx`: 各カードで `earningsWarning(row.days_to_earnings)` が非 null のとき **琥珀色バッジ「⚠ {days}日後に決算」**＋小さく助言「決算跨ぎ注意：保有なら前日までに手仕舞い検討」を表示。確信度/リスクバッジ（`plan/page.tsx:213` 付近）と同じ並びに置き、**direction を問わず**（buy/neutral/sell の全カード）描画する。

## 6. ③ バックテストの決算ギャップ反映＋跨ぎ回避（核心セマンティクス）

`_run_backtest_plan` は**買い専用**（pending=買い指値、保有=ロング）。決算パラメータ2つ（既定 OFF）:

- `earnings_aware: bool=False` — マスタスイッチ。OFF＝決算日を渡さず**現挙動とバイト一致**。ON＝銘柄別決算日（`earnings_map`）を使い、**決算翌日の窓を寄りfillで再現**する。
- `earnings_exit_days: int=0` — `earnings_aware` 時に**決算の N営業日前から手仕舞い・新規約定停止**（跨ぎ回避）。0＝持ち越して窓を食う。

### 6.1 銘柄別の決算日と「窓バー」「ブラックアウト」の定義
銘柄ごとに `edates = earnings_map.get(ticker)`（§4 の正規化済み昇順リスト・None/空ならその銘柄は決算処理 OFF）。df は `sort_index()` 済み。各決算日 E に対し:

- **窓バー（gap bar）** `g(E) = np.searchsorted(df.index.values, E, side="right")` ＝**E より厳密に後の最初のバー位置**。日本株は**引け後発表→翌朝ギャップ**が通常形ゆえ「E 当日のバーは通常取引・翌バー `g(E)` が窓」とする（v1 の割り切り）。E が全バーより後なら `g(E)=len(df)`。
- `gaps_all = sorted({g(E) for E in edates})`（`g==len(df)` も含む＝期間外の将来決算でも回避は効かせる）。
- `gaps_in = {g for g in gaps_all if g < len(df)}`（窓 fill は窓バーが実在する場合のみ）。
- バー i が **ブラックアウト**（`earnings_exit_days=N>0` 時）⟺ `∃ g∈gaps_all: i < g ≤ i+N`（窓バーが「次の N バー以内」）。N=1 なら `g−1` のバーのみ。

### 6.2 保有中（`shares>0`・`i>entry_i`）の決済優先順
打ち手9 の §4 を拡張。`cur_stop` は従来どおり（trailing ON なら `trailing_stop(...)`、OFF なら `stop`。look-ahead 規約も不変）。

1. **日中の価格トリガ**
   - `low ≤ cur_stop` → 決済。
     - **窓バーかつ `earnings_aware`**（`i∈gaps_in`）: fill = `min(cur_stop, open)`（寄りが stop を下回って窓開け＝寄りで約定）。`open < cur_stop` のとき **理由 "gap"**、そうでなければ従来どおり（trailing なら "trail"・else "stop"）。
     - **それ以外**: 従来どおり fill=`cur_stop`、理由 "trail"/"stop"。
   - 上で未決済かつ **trailing OFF** のとき `high ≥ target` → "target"（従来どおり。**target 側の窓 fill はしない**＝利益側は保守的に据え置き・§16）。
2. **決算跨ぎ回避**（`earnings_exit_days>0` かつ上で未決済かつ **bar i がブラックアウト**）→ **当日終値**決済・**理由 "earnings"**。
3. **時間切れ**（`max_hold_days>0` かつ上で未決済・`i−entry_i ≥ max_hold_days`）→ 終値決済 "time"（従来どおり）。
4. **signal**（既存ブロック・当日 `direction=="sell"`）→ 終値決済 "signal"。

**優先順**: 日中価格トリガ（gap/trail/stop・target）＞ 決算回避（終値）＞ 時間切れ（終値）＞ signal（終値）。同一バーで日中 stop と回避が両立すれば**日中を優先**（ザラ場が先）。回避と時間切れが両立すれば**回避を優先**（決算回避は安全側のハード手仕舞い）。

### 6.3 新規約定の抑止（ブラックアウト中は入らない）
エントリー（pending 約定）ブロック（`backtest.py:192` 付近）で、`earnings_exit_days>0` かつ **bar i がブラックアウト**のときは**約定をスキップ**（その日は入らない）。pending 自体は従来どおり有効期限で失効。これにより「直前に入って即手仕舞い」のウォッシュトレードを避け、跨ぎ回避を一貫させる。

### 6.4 排他性（重要・読み方）
- `earnings_aware=True, earnings_exit_days=0` → **持ち越して窓を食う誠実なベースライン**（窓バーで "gap" 決済が出得る）。
- `earnings_aware=True, earnings_exit_days=N>0` → **回避**（ブラックアウトで "earnings" 決済・窓バーに到達しない＝"gap" は出ない）。
- 両者の比較＝**跨ぎ回避の価値**。窓 fill を入れるからこそ「持ち越しの真のコスト」が出て回避が誠実に評価できる（§軽量代替を採らない理由）。
- `earnings_aware=False` → 決算処理ゼロ・現挙動。

### 6.5 エッジ
- `edates` が None/空の銘柄 → その銘柄は `earnings_aware=False` 相当（窓・ブラックアウト無し）。**銘柄ごとに degrade**。
- E が期間外（`g=len(df)`）→ 窓 fill は無いがブラックアウト `[len−N, len−1]` は効く（期間末に既知の将来決算を回避）。
- 複数 E → `gaps_all` の和集合で処理。
- 決済時リセットは従来どおり（`entry_atr/high_water` 等）。新しい保有状態は増やさない（`edates`/`gaps` はバー走査前に銘柄単位で1回計算）。

## 7. `backend/backtest.py` の変更

### `_run_backtest_plan`
- シグネチャに `earnings_map=None, earnings_exit_days=0` を追加（trail/max_hold の隣）。
- 銘柄ループ先頭で `edates = (earnings_map or {}).get(ticker)` から `gaps_all`/`gaps_in`（§6.1）を1回計算（`np.searchsorted`、`edates` 無しなら空集合）。ヘルパ `_in_blackout(i)`（`gaps_all` と N から bisect/内包で判定）。
- 約定ブロック（§6.3）: `earnings_exit_days>0 and _in_blackout(i)` で `continue`相当（約定スキップ）。
- 保有中決済ブロック（§6.2）: 窓 fill（`i in gaps_in` で `min(cur_stop, open)`・理由 "gap"）と回避（"earnings"）を優先順に挿入。
- 戻り値に **`gap_exit_count`**（理由 "gap"）・**`earnings_exit_count`**（理由 "earnings"）を追加（既存 `stop_loss_count`/`trail_exit_count` 等と同型 `sum(... reason==...)`）。
- `earnings_map=None`（既定）→ `gaps` 全空＝従来パスを完全素通り。

### `run_backtest`
- シグネチャに `earnings_map=None, earnings_exit_days=0` を追加し、plan ディスパッチ（`backtest.py:55-61`）で `_run_backtest_plan(..., earnings_map=earnings_map, earnings_exit_days=earnings_exit_days)` へ貫通。**score モードは無視**。

## 8. `backend/evaluation.py` の配線

- `evaluate_holdout(..., earnings_map=None, earnings_exit_days=0)` を追加（`evaluation.py:80-83` のシグネチャ・trail/max_hold の隣）。
- 内部 `_bt`（`evaluation.py:101-108`）の `run_backtest(...)` 呼び出しに `earnings_map=earnings_map, earnings_exit_days=earnings_exit_days` を貫通（train/寄与度/OOS の全 plan-mode 呼び出しが `_bt` 経由＝1箇所追加で足りる）。
- **`benchmark` は score モードのため対象外**（貫通しない・打ち手9 と同じ）。
- グリッド探索（閾値スイープ）に決算パラメータは**加えない**（固定の決算設定を OOS 評価する passthrough のみ・過学習回避）。
- `earnings_map` は**銘柄キーの全決算日リスト**。`evaluate_holdout` が train/test で df を日付スライスしても、`_run_backtest_plan` 側の `searchsorted` は**渡された df の index に対して**窓位置を計算するため、スライスに自然対応（特別処理不要）。

## 9. `backend/main.py` / API の配線

**入口は打ち手9 と同じくペイロード**（実験パラメータ）。`risk_pct`（永続設定）経路は踏襲しない。

- `BacktestIn`（`main.py:109`）に `earnings_aware: bool = False`・`earnings_exit_days: int = 0` を追加。
- `OptimizeIn`（`main.py:573`）にも同2項目を追加。
- `/backtest`（`main.py:522`）:
  - クランプ `eed = max(0, int(payload.earnings_exit_days or 0))`、`ea = bool(payload.earnings_aware)`（打ち手9 の `trail`/`mhd` クランプ隣・`Field` 未 import 方針を踏襲）。
  - `earnings_map = {t: fetch_earnings_dates(t) for t in histories} if (ea and not payload.demo) else None`（**`earnings_aware` かつ非 demo のときだけ**ネット取得。demo・OFF は None＝現挙動）。
  - `run_backtest(..., earnings_map=earnings_map, earnings_exit_days=eed)`。
  - benchmark 呼び出し（`main.py:559`）は不変（score モード）。
- `/optimize`（`main.py:583`）も**同じクランプ・demo ガード**で `earnings_map = {t: fetch_earnings_dates(t) for t in histories} if (ea and not payload.demo) else None` を組み立て、`evaluate_holdout(..., earnings_map=earnings_map, earnings_exit_days=eed)` を渡す（`/backtest` と対称）。
- `market` の import（`main.py:24`）に `fetch_earnings_dates` を追加。
- `perform_refresh`・`build_plan`・`upsert_plan` の**ライブ経路はギャップ/回避ロジックを呼ばない**（§5 の警告永続化のみ）。

## 10. フロント（最小）

- **作戦カードの警告**: §5.3（`api.ts` 型・`rows.ts:earningsWarning`・`plan/page.tsx` バッジ）。
- **バックテスト画面**（`simulation/page.tsx`・`lib/backtest.ts`）: 打ち手9 の trail/time と同じ流儀で任意入力を追加。
  - `backtest.ts`: `BacktestForm` に `earningsAware: boolean`・`earningsExitDays: number`、`BacktestBody` に `earnings_aware?: boolean`・`earnings_exit_days?: number` を追加。`buildBacktestBody` で **`earningsAware` のとき `earnings_aware=true` を付与し、`earningsExitDays>0` のとき `earnings_exit_days` を付与**（OFF 時は省略＝現挙動同一ペイロード）。純関数テスト追加。
  - `simulation/page.tsx`: `atrExit` ブロック（`page.tsx:81`）と並ぶ形で「決算を考慮」チェック＋「決算 N 日前に手仕舞い」数値入力を追加し `buildBacktestBody` に渡す。
  - `api.ts`: `BacktestResult`（`api.ts:122`）に `gap_exit_count?`・`earnings_exit_count?` を追加。`api.backtest` の body 型（`api.ts:199`）に `earnings_aware?`・`earnings_exit_days?` を追加。
  - 結果内訳（`page.tsx:150` 付近）に Metric 2つ「決算ギャップで決済」「決算回避で手仕舞い」を追加。

## 11. バックテスト出力の追加フィールド

`_run_backtest_plan` 戻り値（plan モード）に追加:
- `gap_exit_count`: 決算翌日の窓で寄り約定した決済数（理由 "gap"）。
- `earnings_exit_count`: 決算回避（N日前手仕舞い）の決済数（理由 "earnings"）。

既存の各 count・`avg_holding_days`・`pnl_pct` 等と合わせ、**`earnings_aware`+`earnings_exit_days` を変えた複数ランの比較**で跨ぎ回避の効果を読む（課題7「ギャップをバックテストに反映」の達成手段）。

## 12. 後方互換・回帰

- `earnings_aware=False`（既定）→ `earnings_map=None` → `gaps` 全空 → 約定・決済とも**現行と同一パス**。回帰テストで「既定 OFF のとき従来と同一の `closed_trades`・約定価格列・`closed_pnls`」を固定（打ち手9 `test_plan_exit_params_default_off_is_unchanged` と同型）。
- 新 config ルールなし＝`DEFAULT_CONFIGS` 14・`test_api` 件数不変。
- `daily_plan.days_to_earnings` は冪等マイグレーションで既存 DB に後付け（既存行は NULL＝警告なし）。
- フロント変更は加点のみ（既存 19 テスト不変）。

## 13. テスト計画（TDD）

統制 OHLC で決定論化（[[signal-tests-prefer-deterministic-ohlc]]・seed 走査しない）。決算日は合成 `edates`/`earnings_map` を直接注入（ネット非依存）。

- `test_market.py`（新規・任意）または既存に: `fetch_earnings_dates` の正規化（tz-aware→tz-naive midnight・昇順）・例外/空→None を monkeypatch で固定（`yfinance` をスタブ）。**ネットは叩かない**。
- `test_backtest.py`
  - **窓 fill**: 保有中に決算翌バーが寄りで stop を下回って窓開け → fill が `open`（< cur_stop）・理由 "gap"・pnl が「stop ぴったり約定」より悪い、を固定（`earnings_aware=True, earnings_exit_days=0`）。
  - **跨ぎ回避**: 同じ決算日で `earnings_exit_days=1` → ブラックアウト（窓バーの前日）で終値決済・理由 "earnings"・**窓バーに到達せず "gap" は出ない**。約定抑止（ブラックアウト中は新規約定しない）も固定。
  - **既定 OFF 回帰**: `earnings_aware=False` で従来と同一（`closed_pnls`・約定価格・各 count）。
  - look-ahead: 決算"日"は既知でよいが、窓 fill は当日 open のみ参照（未来バー不参照）を、データ構成で担保。
- `test_evaluation.py`: `evaluate_holdout` が `earnings_map`/`earnings_exit_days` を受領し完走するスモーク（挙動差は backtest 層で担保・`test_evaluate_holdout_accepts_risk_pct` に倣う）。
- `test_api.py`
  - `/backtest`・`/optimize` が新2項目を受領し、未指定で挙動不変。`DEFAULT_CONFIGS` 件数不変。
  - `perform_refresh` が `days_to_earnings` を `daily_plan` に永続化（`fetch_earnings_days` を monkeypatch・既存 sizing 永続化テストに倣う）。マイグレーション（古いスキーマに列追加）も既存テスト型で固定。
- フロント（vitest）
  - `rows.ts:earningsWarning`（境界: null・負・しきい値前後）。
  - `backtest.ts:buildBacktestBody`（OFF で省略・ON で付与・`earningsExitDays=0` で `earnings_exit_days` 省略）。

## 14. 非スコープ・将来拡張

- 手入力決算日テーブル＋CRUD＋UI（yfinance 欠落の本格対策・`fetch_earnings_dates` の合流点）。
- 権利付/落ち・分割カレンダー。
- ライブ作戦のエントリーゲート化・決算回避のライブ採用（backtest 確認後）。
- target 側の窓 fill（利益側ギャップ・現状は保守的に target 据え置き）。
- 一般のギャップ fill（決算以外の窓も寄り約定）＝バックテスト realism の一般改善（本件は決算バーに限定）。
- 決算サプライズ/短信の定性取り込み（打ち手14）。

## 15. リスク・割り切り（v1）

- **yfinance の日本株決算日は欠落しがち**＝警告が出ない/バックテストの決算処理が空振りする銘柄がある。`None` で degrade（警告なし・現挙動）。手入力は将来。
- 窓バーを「E より後の最初のバー」と定義（引け後発表→翌朝ギャップ前提）。寄り前発表で当日ギャップのケースは v1 では当日を通常扱い（割り切り）。
- ブラックアウトは**取引バー基準**（暦日でなく df のバー数）。連休跨ぎ等で実暦日とずれ得るが、決定論で単純。
- target 側ギャップ（窓開けで利確を上抜け）は寄り fill しない＝利益を過小評価し得る（リスク側のみ誠実にし、利益側は保守的）。
- ライブ警告のしきい値はフロント定数（`EARNINGS_WARN_DAYS=5` 暦日）。設定化は将来。
