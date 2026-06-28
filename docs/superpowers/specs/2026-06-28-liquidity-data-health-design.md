# 打ち手12 流動性フィルター/データ健全性チェック — 設計

- 日付: 2026-06-28
- 対応課題: 課題8「データの土台が薄い」のうち**流動性**と**データ健全性**。打ち手12「流動性フィルター・データ健全性チェック」。
- ブランチ: `feat/liquidity-data-health`
- 前提メモリ: [[roadmap-progress]]
- 位置づけ: **戦略レバーではなく「実約定の現実性」を高める安全機能**。6診断で現戦略は「守備的能動オーバーレイ」と確定済み（フェーズD 進行中・打ち手11 採用済み）。本件は score/direction/confidence/sizing/build_plan/backtest を**一切変えず**、薄商い銘柄で提案指値が現実に約定しない/滑る問題に「警告＋推奨からの除外」で対処する。**verify-before-adopt 診断は不要**（戦略の期待値を動かさないため）。

## 1. 背景・目的

`market.py` は yfinance 日足のみで、**流動性（出来高で実際に約定できるか）の検証も、取得データの健全性チェックも無い**（課題8）。

- **薄商い銘柄では提案指値が現実には約定しない/滑る**。にもかかわらず作戦ボードの「今夜の推奨 Top N」は確信度だけで選ぶため、**約定不能な銘柄を堂々と推奨してしまう**恐れがある。
- 単一ソースゆえ**異常値・欠損・出来高0日**を検知できず、壊れたデータの上に作戦を組んでも気づけない。

本件のゴールは、**実際に張れる銘柄か**（流動性）と、**作戦の土台データが健全か**（健全性）を可視化し、推奨が現実の約定とズレないようにすること。これは「予測精度（期待値）を上げる」打ち手ではなく、**提示する作戦の現実性・信頼性を上げる**打ち手である。

## 2. スコープ

**含む（本ブランチ）**

- 純関数モジュール `backend/data_quality.py`（新規・DB/ネット非依存・例外を投げない）：
  - `average_turnover(df, window=20)`：平均売買代金（`close×volume` の直近 window 平均・円）。
  - `data_health(df, window=20, spike_pct=0.5)`：直近 window の `{zero_volume_days, gap_days, spike_days}`。
- `daily_plan` に2列を冪等追加：`avg_turnover REAL` / `data_health TEXT`（JSON）。
- `db.upsert_plan` の配線（既存の追加列と同型）。
- `perform_refresh`：取得済み df から両指標を算出し `upsert_plan` に渡す（**demo/live 両方**＝ネット不要の純算出）。
- frontend（作戦ボード `plan/page.tsx` のみ）：
  - `PlanRow` 型に `avg_turnover` / `data_health` を追加。
  - `rows.ts` 純関数 `liquidityWarning` / `dataHealthWarnings` ＋定数 `LIQUIDITY_MIN_YEN`。
  - **`selectTopN` が薄商い銘柄を「今夜の推奨」から除外**（`confidence>0` 除外と同型の追加フィルタ）。
  - `PlanCard` に薄商いバッジ＋データ注意の小注記（決算バッジと同型の display 専用）。

**含まない（YAGNI・非スコープ）**

- **バックテストの流動性フィルタ**（約定を流動性で弾く）＝backtest の結果（期待値・DD）を変える＝戦略レバー化。本件の「結果不変・診断不要」と矛盾するため**やらない**。決算回避がライブ警告と backtest を分離したのと同思想。
- **取得期間の延長**（課題8 の「period を長め」）。20日売買代金・直近健全性は現行 `period="6mo"`（≈120バー）で十分。延長は別件。
- **流動性閾値の設定UI化**。v1 はフロント定数（`EARNINGS_WARN_DAYS=5` と同型・生値保存で再生成不要に調整可）。設定化は将来。
- score/direction/confidence/sizing/build_plan の変更（一切不変）。
- `page.tsx`（ウォッチ一覧）への波及（作戦カード/Top-N を持たないため対象外）。

## 3. 設計原則

- **分離・テスト容易**：判定ロジックは純関数 `data_quality.py`（DB/ネット非依存）に閉じ込め、DB I/O（`db.py`）・配線（`main.py`）・表示（frontend）と分ける。`signals.py` は肥大ゆえ新モジュールにする（`tracking.py` と同方針）。
- **例外を投げない契約**：不正入力・列欠落・空・nan は安全な既定（None / 全0 dict）を返す（`volume_ratio` の None 返し、`position_size`/`trailing_stop`/`resolve_outcome` の堅牢契約と同じ）。データ取得層に近い best-effort。
- **加法的・後方互換**：列追加のみ。既存の `_migrate_daily_plan` 列リストに足す。`avg_turnover`/`data_health` が NULL（旧行・算出不可）でも**警告を出さず・除外もしない**＝既存挙動を保存（earnings の `days_to_earnings=null → 警告なし` と同じ「null=不明=非干渉」）。
- **生値を保存・しきい値は表示側**：`avg_turnover`（円）と健全性カウントの生値を保存し、警告/除外のしきい値はフロント定数に置く＝再生成せずに調整可能（earnings 警告と同設計）。
- **検証=提示の現実化**：「今夜の推奨」は実際に張る候補。約定できない薄商いを除外することで、提示する作戦を現実の約定に近づける（課題4「検証する作戦＝提示する作戦」の現実性版）。

## 4. 変更内容

### 4.1 `backend/data_quality.py`（新規・純関数）

```
def average_turnover(df, window=20) -> float | None:
    """平均売買代金（円）。直近 window バーの close×volume の平均。
    列欠落 / len(df) < window / nan は None（算出不可＝不明）。例外を投げない。"""

def data_health(df, window=20, spike_pct=0.5) -> dict:
    """直近 window バーのデータ健全性。
    戻り: {"zero_volume_days": int, "gap_days": int, "spike_days": int}（すべて 0 以上）。
    不正入力・空・列欠落は全 0。例外を投げない。"""
```

**`average_turnover`**
- `volume`/`close` が列に無い、または `len(df) < window` → `None`（`volume_ratio` が `len<sma → None` と同じ割り切り。新規追加直後で 20 バー未満の銘柄は「不明」扱い＝警告も除外もしない）。
- `turnover = (df["close"] * df["volume"]).tail(window).mean()`。`nan` → `None`、それ以外は `float(turnover)`（0 以上。全出来高0なら 0.0 を返し、表示側で薄商い判定）。

**`data_health`**（直近 window バーのみ＝「この作戦の土台データ」の鮮度に対応）
- `zero_volume_days`：直近 window バーで `volume <= 0` または `NaN` の本数。
- `spike_days`：直近 window バーの日次変化率 `|close/prev_close − 1| > spike_pct`（既定 0.5＝50%）の本数。`auto_adjust=True` で分割・配当は調整済み＝50% 超の単日変化は JP の値幅制限（概ね ±20〜30%）を超え、**ほぼデータ異常**（誤プリント・未調整分割）。リターン計算のため `tail(window+1)` から `pct_change()`。
- `gap_days`：直近 window バーの連続バー間で**取引日距離 ≥ 2**（＝1取引日以上の欠損）の遷移本数。`holidays_jp.MARKET_HOLIDAYS`（東証休業日・2025-2027 収録）で numpy 営業日カレンダーを作り `np.busday_count(d0, d1, busdaycal=cal)` を使う＝**祝日・連休を取引日と誤検知しない**。次の取引日なら 1、1取引日欠損で 2。`window` 窓は live では常に直近（収録年内）。収録外年は祝日が取引日扱いになり保守的に過検知し得るが、除外ではなく注意喚起ゆえ無害。
- 全体を `try/except` で包み、何が来ても `{"zero_volume_days":0,"gap_days":0,"spike_days":0}` を返す（例外を投げない契約）。

> 代替案（不採用）：祝日カレンダーに依存せず「暦日ギャップ > 9日」で連休を許容しつつ大穴のみ検知する案も検討。既存 `holidays_jp` を再利用する方が取引日距離として正確で、scheduler 同様の既存依存ゆえ採用。

### 4.2 `backend/db.py`

- **スキーマ**：`daily_plan` の `CREATE TABLE` に `avg_turnover REAL` / `data_health TEXT` を追加（新規DB用）。
- **冪等マイグレーション**：`_migrate_daily_plan` の列タプルに `("avg_turnover", "REAL"), ("data_health", "TEXT")` を追加（既存DB用・他の後付け列と同型）。
- **`upsert_plan`**：
  - `row.setdefault(k, None)` の対象キーに `"avg_turnover", "data_health"` を追加。
  - INSERT 列・VALUES・`ON CONFLICT … DO UPDATE SET` に両列を追加（`excluded.*` 上書き。regime のような COALESCE 温存は不要＝毎回 df から算出する鮮度値）。

### 4.3 `backend/main.py`（`perform_refresh`）

- 先頭付近で `from data_quality import average_turnover, data_health`（モジュールトップ import）。
- 各銘柄ループ内、`df = get_history(...)` で空でない df を得た後に算出：
  ```
  turnover = average_turnover(df)
  health = data_health(df)
  ```
- `db.upsert_plan({...})` に追加：
  ```
  "avg_turnover": turnover,
  "data_health": json.dumps(health) if any(health.values()) else None,
  ```
  健全（全0）なら `None`（DB を不要なノイズで埋めない＝ai_summary/days_to_earnings の None 慣行）。
- **demo でも算出する**：earnings はネット取得ゆえ demo で None だが、`average_turnover`/`data_health` は df からの純算出ゆえ demo でも出す（合成データは出来高1M〜8M・価格〜2500 → 売買代金は十分・健全＝デモでも機能が見える）。

### 4.4 frontend

**`src/lib/api.ts`** — `PlanRow` に追加：
```
avg_turnover: number | null;   // 平均売買代金（円・直近20日）
data_health: string | null;    // JSON: {zero_volume_days,gap_days,spike_days}。健全/旧行は null
```

**`src/lib/rows.ts`**（純関数・テスト対象）
```
/** 薄商い警告のしきい値（円・平均売買代金/日）。1億円未満を「実約定に難あり」とみなす。
 *  ※ 個人の実約定可能性の目安。設定化は将来。 */
export const LIQUIDITY_MIN_YEN = 100_000_000;

/** 平均売買代金がしきい値未満なら {turnover} を返す純関数（それ以外 null）。
 *  null（不明・旧行）は警告しない＝「不明=非干渉」。 */
export function liquidityWarning(
  avgTurnover: number | null,
  threshold = LIQUIDITY_MIN_YEN,
): { turnover: number } | null { … }

/** data_health(JSON文字列) を人間可読な注意文の配列に。null/健全/壊れJSON は []。 */
export function dataHealthWarnings(json: string | null): string[] { … }
//   zero_volume_days>0 → `出来高0の日が${n}日`
//   gap_days>0        → `データ欠損 ${n}件`
//   spike_days>0      → `異常な値動き ${n}件（データ要確認）`
```

`selectTopN` の除外フィルタを拡張（**薄商いを推奨から外す**）：
- `Rankable` に `avg_turnover?: number | null` を**任意**追加（既存テストのオブジェクトリテラルを壊さない）。
- フィルタ条件に `&& (r.avg_turnover == null || r.avg_turnover >= LIQUIDITY_MIN_YEN)` を追加。**null は通す**（旧行・不明は除外しない＝後方互換）。既知かつ閾値未満のみ除外。
- `rankByConfidence`（並べ替え）は無改変。

**`src/app/plan/page.tsx`（`PlanCard`）** — 決算バッジの直後に display 専用で追加（direction 非依存・保有者にも出す）：
- 薄商いバッジ：`liquidityWarning(row.avg_turnover)` が非 null なら琥珀バッジ「⚠ 薄商い（売買代金 約 {Math.round(turnover/1e6)} 百万円/日）」＋ title「薄商い：提案指値は約定しづらく滑りやすい」。
- データ注意：`dataHealthWarnings(row.data_health)` が非空なら小さな注記行（`text-amber-700` 等）で各文言を列挙。
- 「今夜の推奨 Top N」セクションの注記に「薄商い銘柄は推奨から除外しています」を一言添える（任意・UX 補助）。

## 5. テスト計画

**backend（新規 `backend/test_data_quality.py`）— 純関数・統制データ**
- `average_turnover`：既知 OHLCV（例：close=100, volume=10,000 一定・window=20）→ 1,000,000 円。`len<window`→None。`volume` 列欠落→None。全出来高0→0.0。`nan` 混在で安全。
- `data_health`：
  - 健全データ → 全0。
  - 出来高0を n 本仕込む → `zero_volume_days==n`。
  - 50% 超の単日変化を仕込む → `spike_days` 検出。25% 程度は非検出（しきい値境界）。
  - 取引日を間引いて欠損を作る → `gap_days` 検出。**連休（祝日）を挟んでも誤検知しない**（`holidays_jp` の 2026 GW/年末を使った統制ケース）。
  - 空 df / 列欠落 / 非数 → 例外を投げず全0。
- **決定論**：合成 seed 走査でなく統制 OHLC（[[signal-tests-prefer-deterministic-ohlc]]）。

**backend（`test_api.py` 等）— 配線・移行**
- `perform_refresh`（demo）後に `daily_plan` の `avg_turnover` が数値・`data_health` が NULL もしくは妥当 JSON（days_to_earnings の配線テストと同型）。
- 移行：`avg_turnover`/`data_health` 列が無い旧 `daily_plan` に `_migrate_daily_plan` で列が付く（既存移行テストに2列追加 or 新規）。
- 既存件数アサート（`DEFAULT_CONFIGS` は不変＝14 のまま。config 追加なし）。

**frontend（`src/lib/__tests__/rows.test.ts`）**
- `liquidityWarning`：閾値未満→`{turnover}`、以上→null、null→null、境界（==閾値）→null。
- `dataHealthWarnings`：各カウント>0 の文言生成・複数同時・全0→[]・null→[]・壊れJSON→[]。
- `selectTopN`：薄商い（avg_turnover<閾値）を除外、流動的は残す、`avg_turnover` 未指定/null は除外しない（後方互換）、confidence 除外との併用。

**回帰**：backend `pytest -q`（166→+α 緑）・frontend `npm test`（25→+α 緑）。

## 6. 落とし穴・非自明点

1. **`data_health` の JSON 保存は「issue があるときだけ」**（全0は None）。フロント `dataHealthWarnings(null)→[]` で対応。テストは「全0→DB None」「issue→JSON」両方を確認。
2. **`selectTopN` の null 通過**：旧行・算出不可は `avg_turnover=null` で**除外しない**。除外は「既知かつ薄商い」のみ。これを誤って `null` も除外すると、移行直後に推奨が全部消える事故になる（confidence の `?? 0` とは非対称＝confidence は「不明=確信なし=非推奨」だが、流動性は「不明=現実性は判定保留=推奨を消さない」。哲学が逆なので要注意）。
3. **`np.busday_count` の取引日距離**：次の取引日=1、1取引日欠損=2。しきい値は `>= 2`。`holidays_jp` 未収録年では祝日が取引日扱い＝過検知し得るが、live の 20 バー窓は常に直近（収録内）。除外でなく注意喚起なので過検知は無害。
4. **demo でも算出**：earnings（ネット）と違い純算出ゆえ demo で出す。テスト・デモ UX 上も自然。
5. **backtest 非干渉**：`run_backtest`/`evaluate_holdout`/`benchmark`/`build_plan`/`paper_trades` は無改変。流動性は**ライブ作戦ボードの表示・推奨選別のみ**に作用（戦略の期待値・DD を動かさない＝診断不要の根拠）。
6. **スパイク閾値 50% は緩め**（誤検知を避ける soft advisory）。除外には使わず注意喚起のみ。値幅制限的に JP では 50% 超単日はほぼデータ異常。

## 7. 受け入れ基準

- `data_quality.py` の2純関数が統制データで仕様どおり（None/全0 の堅牢契約含む）。
- `perform_refresh` 後、`daily_plan.avg_turnover` が数値・`data_health` が NULL/妥当 JSON。既存DBに2列が冪等追加される。
- 作戦ボードで薄商い銘柄に警告バッジが出て、**「今夜の推奨 Top N」から除外**される。流動的な銘柄・`avg_turnover=null` の旧行は従来どおり推奨に載る。
- データ注意（出来高0/欠損/スパイク）が該当時に注記される。
- score/direction/confidence/sizing/backtest/benchmark の出力が**バイト一致で不変**（加法的・後方互換）。
- backend `pytest -q` / frontend `npm test` 全緑。

関連: [[roadmap-progress]]・[[signal-tests-prefer-deterministic-ohlc]]
</content>
</invoke>
