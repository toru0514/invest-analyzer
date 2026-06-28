# 打ち手11 実績トラッキング（記録と可視化）— 設計

- 日付: 2026-06-28
- 対応課題: 課題6「作戦の結果から学ぶループが無い」。打ち手11「作戦の実績トラッキング（型別成績）」。
- ブランチ: `feat/plan-outcome-tracking`
- 前提メモリ: [[roadmap-progress]]
- 位置づけ: 6診断で**現戦略は「守備的能動オーバーレイ」（暴落保護が real edge・bull では buy&hold 未満）**と確定。プロダクト方針＝守備オーバーレイとして磨く（フェーズD）。本件はその第一歩＝**どの型/レジームで効くかを実データで蓄積する学習ループの測定基盤**。

## 1. 背景・目的

`daily_plan` に作戦は保存されるが、**その後どうなったか（約定したか・利確/損切どちらか・何日で・いくら）を蓄積する仕組みが無い**（`paper_trades` はバックテスト専用）。課題6 のとおり「記録と可視化」から始め、**型（レジーム×方向）ごとの実約定率・実勝率・実R倍率**を集計する。自動チューニング（確信度への重み反映）は本件の**非スコープ**（測定基盤を据えてから）。

守備オーバーレイの価値は「どの局面で効くか」。実績トラッキングは、診断（過去）でなく**ライブ運用の実結果**で型別成績を蓄積し、運用判断と将来の確信度精緻化の土台にする。

## 2. スコープ

**含む（本ブランチ）**
- 純関数モジュール `backend/tracking.py`：①`resolve_outcome` 単一作戦の結果を将来 OHLC から判定 ②`plan_type` 型分類 ③`aggregate_performance` 型別集計。すべて DB/ネット非依存。
- `daily_plan` に結果列を冪等追加（`regime/fill_status/outcome/exit_price/result_r/resolved_date`）。
- `perform_refresh`：作戦生成時に `regime` を保存し、ループ後に既存作戦の結果を `price_data` から解決（best-effort・冪等）。
- `GET /performance`：型別成績サマリを返す。
- frontend：最小の「型別成績」ダッシュボード（型 × fill率/勝率/平均R/期待値/n）。

**含まない（YAGNI・非スコープ）**
- **確信度/重みへの自動フィードバック**（課題6「自動チューニングはその後」）。集計を見るだけ。
- スコア/方向/build_plan/出口ロジックの変更（一切不変）。
- バックテスト経路の変更（`paper_trades`・run_backtest は無改変）。
- 約定の手数料/スリッページ厳密化（result_r は提示指値ベースの理論値＝v1 の割り切り。コスト込み実約定は将来）。

## 3. 設計原則

- **分離・テスト容易**：判定ロジックは純関数 `tracking.py`（DB/ネット非依存）に閉じ込め、DB I/O（`db.py`）・配線（`main.py`）と分ける。signals.py は肥大ゆえ新モジュールにする。
- **stateless 再計算・冪等**：結果は `price_data`（保存済み OHLC）から後付けで再計算。**terminal（n/a・expired[窓満了]・target・stop）は確定後キャッシュ**（resolved_date を立て再解決しない）、**非終端（pending[窓未経過の未約定]・open[約定後未決済]）は resolved_date=NULL のまま**＝データ増で terminal へ遷移するまで毎 refresh 再解決。何度実行しても同じ結果（look-ahead は無関係＝post-hoc 評価）。**生成当日は future_bars=[] ゆえ全行 pending＝正常**（fill_rate≡0 の早期死を回避）。
- **加法的・後方互換**：列追加のみ（既存移行 `_migrate_daily_plan` の列リストに足す）。score/direction/confidence/sizing/カードは不変。
- **R倍率を主指標**：`result_r = (exit−entry)/(entry−stop)`＝建玉リスク1単位あたり損益。銘柄・価格帯を跨いで正規化でき、課題6 の「実R:R」をそのまま表す（stop=−1R, 利確=+数R）。

## 4. 変更内容

### 4.1 `backend/tracking.py`（新規・純関数）
```
EXPIRY_DEFAULT = 5

def resolve_outcome(plan: dict, future_bars: list[dict], expiry: int = EXPIRY_DEFAULT) -> dict:
    """plan(direction/limit_price/stop_price/target_price) と plan_date 当日含む以降の OHLC
    （future_bars: [{date,open,high,low,close}] 昇順）から結果を判定。買い/売り対応。
    戻り: {fill_status, outcome, entry_price, exit_price, result_r, resolved_date, days_held}。"""
```
判定（buy。sell は不等号反転で対称）。**戻り値はライフサイクル状態を含み、db 側が終端/非終端を判定して resolved_date を立てる**:
- **対象外（terminal・n/a）**: direction が neutral／limit・stop・target いずれか欠損／`entry−stop ≤ 0`（ゼロ除算・異常プラン）／数値化失敗・nan → `fill_status="n/a"`（例外を投げない＝position_size/trailing_stop と同方針）。
- **約定**: plan_date 当日を i=0 として **i ∈ [0, expiry) の bar で `low ≤ limit`** なら約定（entry=limit, fill_i=i, `fill_status="filled"`）。
- **未約定の分岐（Issue2 是正＝早期 expired を防ぐ）**:
  - expiry 窓が**満了済み**（plan_date 以降の利用可能バー数 ≥ expiry）で未約定 → `fill_status="expired"`（**terminal**, outcome=None, result_r=None）。
  - expiry 窓が**未経過**（利用可能バー < expiry。生成当日の future_bars=[] を含む）で未約定 → `fill_status="pending"`（**非終端**＝後日 price_data 増で再解決）。
- **決済**（約定後）: **fill_i+1 以降の bar**を前進走査（backtest `_run_backtest_plan` の `i>entry_i` と一致＝約定足では決済しない・Issue3 是正）。優先順 **stop>target**（同足両ヒットは stop。backtest L237→L242 と一致）: `low ≤ stop`→outcome="stop"(exit=stop) / `high ≥ target`→outcome="target"(exit=target)（**terminal**）。どちらも未到達のままデータ尽き→`outcome="open"`(exit=最終close=時価。**非終端**＝後日再解決)。
- `result_r = (exit−entry)/(entry−stop)`（buy）／`(entry−exit)/(stop−entry)`（sell）。stop決済=−1.0R、target決済=正。`days_held = 決済bar − fill_i`。
- **終端/非終端のまとめ**: terminal = {n/a, expired, outcome∈(target,stop)}（resolved_date を立て再解決しない）／非終端 = {pending, open}（resolved_date=NULL のまま・再解決対象）。

```
def plan_type(direction: str | None, regime: str | None) -> str:
    """v1 タクソノミ: f"{regime or 'unknown'}:{direction or 'none'}"（例 'risk_on:buy'）。後で friendly 名に拡張可。"""

def aggregate_performance(rows: list[dict]) -> list[dict]:
    """plan 行（plan_type/fill_status/outcome/result_r/days_held を含む）を型別に集計。
    型ごと {type, n_plans, n_filled, fill_rate, n_resolved, win_rate, avg_r, avg_days} を返す（型名昇順）。
    - **n_plans/fill_rate の母数は追跡対象（fill_status != "n/a"）のみ**（R1是正＝neutral 等の n/a 行は除外）。
      fill_rate = n_filled / n_plans。
    - **win_rate/avg_r/avg_days は filled かつ terminal(outcome∈target/stop) のみ**（pending/open/expired 除外）。
      avg_r = mean(result_r) ＝ 1トレードあたり期待値（R単位）。win_rate = target数 / (target+stop)。
      （R2是正: avg_r が期待値Rそのものゆえ expectancy_r は重複・削除。）"""
```

### 4.2 `backend/db.py`（列追加＋解決＋集計 I/O）
- `_migrate_daily_plan` の列タプルに追加（冪等・既存と同型）: `("regime","TEXT")`,`("fill_status","TEXT")`,`("outcome","TEXT")`,`("exit_price","REAL")`,`("result_r","REAL")`,`("days_held","INTEGER")`,`("resolved_date","TEXT")`（R4是正＝days_held を保存し「何日で」を満たす。entry は filled時 limit_price と同値ゆえ列を持たない）。
- **`upsert_plan` に regime を配線（Issue1 是正）**: `upsert_plan` は dict-splat ではなく**固定列 INSERT＋名前付き params＋ON CONFLICT DO UPDATE**（confidence/shares 等もこの方式で明示配線されている）。よって `regime` を保存するには **(a) INSERT 列リスト、(b) VALUES の名前付き params、(c) ON CONFLICT DO UPDATE SET の3箇所すべてに `regime` を追加**する（fill_status 等の結果列は upsert_plan では触らず resolve_plan_outcomes が UPDATE で埋める）。
- `resolve_plan_outcomes()`: `daily_plan` のうち **resolved_date IS NULL** の行（＝未解決＋非終端の pending/open）を対象に、`price_data` から該当 ticker の `date ≥ plan_date` の OHLC（昇順）を取り `tracking.resolve_outcome` で判定し、`regime/fill_status/outcome/exit_price/result_r/days_held` を UPDATE。**terminal（n/a・expired・target・stop）のときだけ `resolved_date` を立てる**（＝以後スキップ・冪等）。**非終端（pending・open）は resolved_date を NULL のまま**＝price_data 増で再解決（Issue2/point3 是正）。戻り値＝今回終端化した件数。
- `performance_summary()`: `daily_plan` 行（regime/direction/fill_status/outcome/result_r/days_held）を取得→`tracking.plan_type` を付与→`tracking.aggregate_performance` で集計して返す（未解決 pending/open も n_plans には含むが win_rate 等の母数からは tracking 側で除外）。

### 4.3 `backend/main.py`（配線）
- `perform_refresh`: `upsert_plan({... , "regime": regime})` を追加（`regime` は既存変数 L394。market_regime のスカラ＝その日の地合い。demo/取得失敗時 None）。
- ループ後（return 前）に `try: db.resolve_plan_outcomes() except Exception: pass`（best-effort・既存挙動を壊さない）。
- 新 `@app.get("/performance")` → `{"summary": db.performance_summary()}`。

### 4.4 frontend（最小ダッシュボード）
- `api.ts`: `getPerformance()` ＋型。純関数でも可。
- 新ページ `/performance`（または作戦ボードの一区画）：型別成績の表（型・n・fill率・勝率・平均R・期待値R）。空（データ未蓄積）時は「実績が貯まると表示されます」。
- ナビに導線1つ。表示専用＝既存挙動不変。

## 5. テスト計画（TDD）

- `backend/test_tracking.py`（新規・純関数中心）
  - `resolve_outcome`: buy 約定→target / 約定→stop / 約定後 open / expiry内未約定→expired / neutral→n/a。**result_r の値**（stop=−1.0、target=(target−entry)/(entry−stop)）を固定。sell 対称1件。nan/欠損→n/a（例外なし）。優先順 stop>target（同 bar で両方ヒット時 stop）。
  - `plan_type`: 代表マッピング。`aggregate_performance`: 型別の n/fill_rate/win_rate/avg_r/expectancy が手計算と一致・open/expired は勝率分母から除外。
- `backend/test_api.py`
  - `_migrate_daily_plan` 列追加（既存DB→ init_db で 6列増・冪等）。`tmp_path`+`monkeypatch.setattr(db,"DB_PATH",...)`。
  - `resolve_plan_outcomes` 結合: daily_plan に作戦＋price_data に将来 OHLC を入れ→解決後に outcome/result_r が入る・再実行で terminal 不変（冪等）。
  - `/performance` smoke（空でも 200・型別 list）。`perform_refresh`(demo) が regime 列を保存（demo は regime None 可）。
- frontend: ダッシュボードの純関数/コンポーネント test（行が描画される・空表示）。
- 回帰: backend 全スイート（現 152±）/ frontend 全スイート。`DEFAULT_CONFIGS` 不変・新 config ルールなし。

## 6. 後方互換・回帰

- score/direction/confidence/sizing/build_plan/出口/バックテストは**一切不変**。新 config ルールなし。
- daily_plan は列追加のみ（既存行は新列 NULL→`resolve_plan_outcomes` で順次解決）。`upsert_plan` は dict 駆動で後方互換。
- `/performance` は新規・読み取り専用。フロントは新ページ追加のみ（既存ページ不変）。
- demo 経路：regime None・price_data は合成 upsert あり→解決は best-effort（demo でも壊れない）。

## 7. 非スコープ・将来拡張

- 確信度/重みへの自動フィードバック（型別成績→スコア調整）＝学習ループの第2段（課題6 後半）。
- コスト込み実約定・部分約定・スリッページのトラッキング。
- 型タクソノミの精緻化（押し目の深さ・出来高・週足を加味した型）。friendly 名・多言語。
- 実保有（holdings）との突合（提示作戦 vs 実トレード）。
- 成績の時系列・ドローダウン可視化、フィルタ（期間/銘柄/レジーム）。

## 8. リスク・割り切り（v1）

- **result_r は提示指値ベースの理論値**（手数料/スリッページ/ギャップ未考慮）。バックテストの厳密約定とは別物＝「提示作戦がどう動いたか」の指標。コスト込みは将来。
- **price_data の将来バー依存**：解決には plan_date 以降の OHLC が `price_data` に必要。perform_refresh が日々 upsert して蓄積する前提＝運用初期は `open`/未解決が多い（正常）。
- **regime は refresh 時点のスカラ**を plan_date に紐付け（厳密な「その営業日の地合い」ではなく直近値）。型分類の v1 近似として許容。
- 型タクソノミ v1（regime×direction）は粗い＝サンプルが貯まれば細分化（将来）。
- daily_plan の列増（19→25）。さらに増えるなら別テーブル（plan_outcomes）への分離を将来検討。
