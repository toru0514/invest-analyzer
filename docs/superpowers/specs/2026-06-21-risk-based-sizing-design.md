# 打ち手8: リスクベースのサイジング — 設計（spec）

- 日付: 2026-06-21
- ブランチ: `feature/risk-based-sizing`
- 対応課題: 課題5（出口とサイジングが固定的＝サイジングが資金等分で確信度・ボラを無視）
- ロードマップ: フェーズC 作戦の質（出口・サイジング・イベント）／打ち手8（効果★★・難易度 低）

## 1. 背景と問題

プロは **1トレードの損失額を一定に保つ**。損切り幅が広い（＝ボラの高い）銘柄は小さく、狭い銘柄は大きく建てる。現状はこれが全くできていない。

- **作戦ボード**（`build_plan`）は `limit_price`/`stop_price`/`target_price` を出すが、**株数（どれだけ建てるか）を一切出さない**。アプリの §0 の約束「いくらで・**どれだけ**・どういう理由で仕掛けるか」のうち「どれだけ」が欠落している。
- **バックテスト**のサイジングは**資金等分のみ**。`run_backtest`（score モード）は `per_trade_budget = initial_capital / n`、`_run_backtest_plan`（plan モード）は銘柄ごとに `initial_capital / n` の独立バケットで実質**全力買い**（`shares = (cash - fee) / fill`）。確信度もボラ（損切り幅）も無視している。

打ち手8 が埋める穴は明確で、必要な材料はすでに揃っている:

- **エントリーと損切り幅**: `build_plan` の `limit_price`（提案エントリー）と `stop_price`（損切り）＝ `entry − stop` がリスク幅。
- **確信度**: 打ち手6/7 で `evaluate` が `detail["confidence"] ∈ [0,100]` を算出し `daily_plan.confidence` に永続化済み。許容リスクの微調整に使える。

## 2. ゴール / 非ゴール

### ゴール
1. **純粋ヘルパ `position_size`** を新設し、`株数 = (口座 × 1トレード許容リスク%) ÷ (エントリー − 損切り幅)` を算出する。確信度で許容リスク% を**微調整**（自信が低い時だけ縮小する保守的な単調マップ）。
2. **作戦ボード**（Surface 1）: buy プランに推奨株数・想定投資額・想定損失（¥ と口座%）を出し、永続化・表示する。
3. **バックテスト**（Surface 2）: `_run_backtest_plan` の約定を**同じヘルパ**でリスクサイジングし、「検証＝提示」をサイジング単位で担保する。
4. 後方互換: 整数 `score`/`direction`・確信度は**不変**（サイジングは別チャネル）。`risk_pct` 未供給時・旧 plan 行・既存テストの挙動を壊さない。

### 非ゴール（スコープ外）
- **単元株（100株）丸め**: このアプリは端株可（`holdings.shares REAL`・`paper_trades.shares REAL`・バックテストも float 株数）。v1 は float 株数を維持し、表示は四捨五入。単元丸めは将来拡張。
- **共有資金プールへの `_run_backtest_plan` 書き換え**: 現状の「銘柄ごと独立バケット（日付主体でなく銘柄主体ループ）」を維持し、バケット内でリスクサイジング（バケット現金を上限にキャップ）。日付主体ループ＋単一の共有プール化は脆い検証インフラの大規模改変になるため将来拡張に明記（§7）。
- **動的 R:R**（トレンドで利確を伸ばす）: 打ち手9 の出口の話。本打ち手は損切り幅を所与として株数だけ決める。
- **ショート（sell）のサイジング**: このアプリはロング前提（`holdings` はロング・`_run_backtest_plan` も buy のみ建てる。sell は戻り売り/保有者の出口）。サイジングは**買い（新規ロング）エントリーのみ**対象。
- **score モード `run_backtest` のサイジング変更**: plan モード（`_run_backtest_plan`）のみリスクサイジング。score モードと benchmark（buy&hold／全シグナル等分）は意図的に等分のまま（後者は素朴なベースラインであることに意味がある）。

## 3. 主要な設計判断

### 採用案: 共有の純粋ヘルパを作戦ボードとバックテストの両方で使う（バケット構造は維持）

| 案 | 内容 | 判定 |
|---|---|---|
| 案1（軽） | 作戦ボードに株数・想定損失を表示するだけ。バックテストは等分のまま | **不採用**。「検証＝提示」（課題4）を崩す。提示はリスクサイジング・検証は等分という不一致が生じる |
| **案2（採用・均衡）** | 共有ヘルパ `position_size` を作戦ボード表示＋`_run_backtest_plan` の両方で使う。バックテストは銘柄バケット構造を維持し、バケット内でリスクサイジング（バケット現金を上限にキャップ） | **採用**。検証＝提示をサイジング単位で担保。脆い検証インフラの構造改変なしで低リスク。打ち手7メモの「全経路に貫通」流儀に整合 |
| 案3（重） | 案2＋`_run_backtest_plan` を日付主体ループに書き換え単一の共有資金プール化 | 不採用。難易度低の打ち手に対し過大。脆い検証インフラの大規模書き換えはリスク高。共有プールは将来拡張（§7） |

**トレードオフの明示**:
- 案2 はバケット現金（`initial_capital / n`）を株数の上限にキャップする。損切り幅が狭い（低ボラ）銘柄ではリスク目標株数がバケットを超え得るため、その場合はバケットでキャップされ「全力買い」に縮退する。一方、損切り幅が広い（高ボラ）銘柄ではリスク目標がバケット未満になり**建玉が縮む**＝過大ベットの抑制という打ち手8 の本質的価値はこのケースで効く。完全な「口座全体に対するリスク%」を全銘柄で厳密に実現するには共有プール（案3）が要るが、v1 はキャップ付きで割り切る（§7 に明記）。
- リスク% の基準は**口座全体（`initial_capital`）に対して**取り、作戦ボードと同じ意味にする（バケット＝`initial_capital/n` に対してではない）。これで「検証＝提示」がサイジングの意味論でも一致する。

### 確信度による許容リスクの微調整

`eff_risk_pct = risk_pct × (CONF_FLOOR + (1 − CONF_FLOOR) × clamp(confidence, 0, 100) / 100)`、`CONF_FLOOR = 0.5`。

- 確信度 0 → 基準の 50%、確信度 100 → 基準の 100%。**基準を超えない単調増加**＝「自信が低い時だけリスクを縮小する」保守的な設計。確信度で増やすと過大ベットを助長しかねないため、v1 は縮小方向のみ。
- `confidence=None` のときは微調整なし（`eff_risk_pct = risk_pct`）。
- `CONF_FLOOR` はモジュール定数として置き、将来チューニング可能にする。

## 4. アーキテクチャ

データフロー（既存に株数算出を1つ加える）:

```
position_size(entry, stop, account, risk_pct, confidence=None)            # 新規・純粋ヘルパ
  → risk_per_share = entry − stop
    eff_risk_pct   = risk_pct × (0.5 + 0.5 × clamp(confidence)/100)   # confidence=None なら risk_pct
    risk_amount    = account × eff_risk_pct / 100                     # risk_pct は「%」表記（1.0 = 1%）
    shares         = risk_amount / risk_per_share
    position_value = shares × entry
  → {shares, risk_amount, risk_per_share, position_value, effective_risk_pct}
  ガード: entry/stop/account/risk_pct が None・非正、risk_per_share ≤ 0 → 全ゼロの安全な結果

[Surface 1: 作戦ボード]
perform_refresh:
  plan = build_plan(df, direction, score, cfgs)
  if direction == "buy" and plan["limit_price"] and plan["stop_price"]:
      sz = position_size(plan["limit_price"], plan["stop_price"],
                         account_size, risk_pct, confidence=detail["confidence"])
      → upsert_plan(..., shares=sz["shares"], risk_amount=sz["risk_amount"])
  else: shares=None, risk_amount=None
get_settings/put_settings: account_size・risk_pct を app_meta で読み書き
list_plan (SELECT *) → /plan → PlanRow（shares, risk_amount 追加） → PlanCard 表示

[Surface 2: バックテスト]
_run_backtest_plan(..., risk_pct=DEFAULT_RISK_PCT):
  発注判定時（指値を出す日）に evaluate の detail["confidence"] を取得し pending に保持する。
    → サイジングに使う confidence は「発注日」のもの＝作戦ボードがその銘柄に出すのと同じ確信度で固定。
      （pending は毎営業日の買いシグナルで更新される既存挙動なので、常に直近発注日の確信度になる。）
  約定時: desired = position_size(limit, stop, initial_capital, risk_pct, confidence=pending.confidence)["shares"]
          shares  = min(desired, (cash - fee) / fill)        # バケット現金でキャップ
run_backtest(..., risk_pct=) → exit_mode in (plan, atr) のとき _run_backtest_plan へ素通し
main.py /backtest・/optimize: risk_pct を app_meta から読み run_backtest/evaluate_holdout へ供給
evaluation.evaluate_holdout: plan 戦略の risk_pct を貫通（内部の _bt → run_backtest plan モードへ）
evaluation.benchmark: 配線**不要**（唯一の run_backtest 呼び出しが score モード固定＝リスクサイジング対象外。
  benchmark の buy&hold/全シグナルは意図的に等分のまま。risk_pct を足してもデッドコードになる）
```

### モジュール配置
`position_size` は `signals.py` に置く（`relative_strength` など他の純粋ヘルパと同居・`build_plan` の隣）。`DEFAULT_RISK_PCT = 1.0`・`CONF_FLOOR = 0.5` を `signals.py` の定数として定義。バックテストは `from signals import position_size, DEFAULT_RISK_PCT`。

## 5. データモデル変更

### 5.1 `daily_plan`（新カラム2つ・冪等マイグレーション）
```sql
shares       REAL    -- 推奨株数（buy のみ。それ以外・データ不足は NULL）
risk_amount  REAL    -- 想定損失額（¥。損切り到達時の損失 = shares × risk_per_share）
```
- `CREATE TABLE` 文（`db.py`）に2列追加。
- `_migrate_daily_plan` の追加列タプルに `("shares", "REAL"), ("risk_amount", "REAL")` を追加（confidence と同型の冪等 ALTER）。
- `upsert_plan`: `setdefault` の preserve 列に `shares`/`risk_amount` を追加。INSERT 列・VALUES・`ON CONFLICT DO UPDATE SET` に両列を追加。
- `list_plan` は `SELECT *` のため自動で返る（変更不要）。

### 5.2 `app_meta`（設定2つ）
```
account_size  -- 既定 "1000000"（¥100万）
risk_pct      -- 既定 "1.0"（1トレード許容リスク% = 口座の1.0%）
```
- `get_settings` に `account_size: float(m.get("account_size", "1000000"))`・`risk_pct: float(m.get("risk_pct", "1.0"))` を追加。
- `put_settings` / `SettingsUpdate` に両フィールドを追加（None でないとき `set_meta`）。
- バリデーション: `account_size` は正、`risk_pct` は `0 < risk_pct ≤ 100`。範囲外は既定にフォールバック（`_safe_top_n` と同じ防御方針のヘルパ `_safe_pos_float` を用意）。

## 6. テスト計画

### 6.1 `position_size` 純粋ヘルパ（`test_signals.py`）
- 基本: `entry=1000, stop=950, account=1_000_000, risk_pct=1.0`（confidence なし）→ `risk_per_share=50`、`risk_amount=10_000`、`shares=200`、`position_value=200_000`。
- 確信度微調整: 同条件で `confidence=0` → `eff=0.5%`→`shares=100`、`confidence=100` → `shares=200`、`confidence=50` → `eff=0.75%`→`shares=150`。
- 損切り幅でスケール: 損切り幅が広い銘柄ほど株数が小さい（`stop=900` なら `risk_per_share=100`→`shares=100`、`stop=975` なら `25`→`400`）＝ボラ調整の核を固定。
- ガード: `entry ≤ stop`（risk_per_share ≤ 0）・`account ≤ 0`・`risk_pct ≤ 0`・None 引数 → `shares=0`・`risk_amount=0` の安全な結果（例外を投げない）。

### 6.2 作戦ボード（`test_api.py`）
- `perform_refresh`（demo）後、buy プラン行に `shares`・`risk_amount` が数値で入る。sell/neutral 行は `shares=None`。
- `/settings` GET に `account_size`・`risk_pct` が既定値で含まれる。PUT で更新→GET で反映。範囲外（負・0・>100）は既定へフォールバック。
- 設定アサートは `account_size`/`risk_pct` の個別検査を足す形で十分（`test_api.py` の `/settings` テストにキー数の網羅アサートは無く、個別キー検査のみのため、キー数検査の更新は不要）。

### 6.3 マイグレーション（`test_api.py` か db テスト）
- `shares`/`risk_amount` 列が無い旧 `daily_plan` を持つ DB を開いても `_migrate_daily_plan` で冪等に追加され、既存行は両列 NULL のまま読める。

### 6.4 バックテスト（`test_backtest.py`）
- `_run_backtest_plan` がリスクサイジングで約定する（同一データで等分時と株数が変わる＝損切り幅に応じてスケール）。
- バケット現金キャップ: 損切り幅が極端に狭い銘柄でリスク目標がバケットを超える場合、`shares × fill ≤ バケット現金`（旧挙動に縮退）。
- `risk_pct` 貫通: `run_backtest(exit_mode="plan", risk_pct=...)` が `_run_backtest_plan` に渡る。`evaluate_holdout` 経路で plan 戦略に貫通する（`benchmark` は score モード固定のため配線しない）。
- **観測可能性**: リスクサイジングの効果（高ボラ＝損切り幅が広い銘柄の建玉が縮む）を観測するテストは、**バケットキャップに張り付かない資本設定**で行う。既定の `INITIAL_CAPITAL=3000` は小さく、entry≈数百〜千円の合成データでは `desired` がほぼ常に `bucket/fill` を超えて全力買いに縮退するため差が出ない。サイジングのスケール（損切り幅で株数が変わる）を検証するテストは `initial_capital` を十分大きく（例: 数百万〜）設定し `shares × fill < bucket` の領域で確認する。キャップ縮退の挙動は別途、小資本で確認する。
- **既存テストの更新（重要・破壊的）**: `test_run_backtest_plan_rs_invariant`（test_backtest.py:163-175）は「plan モードは RS 供給で pnl 不変」を主張するが、本打ち手で **confidence（RS 依存）がサイジングに入る**ため `pnl_amount` の不変は**設計上崩れる**。このテストの意図（build_plan の指値が RS 非依存）は `closed_trades`＝約定構造の不変と約定**価格**の不変で表現し直し、`pnl_amount` の等価アサートは削除/置換する（pnl はサイジング経由で変わるのが正しい）。その他、plan モードの pnl/株数を固定している既存アサートも新サイジングに合わせて更新（red→green の一部）。score モード・benchmark のテストは不変であることを確認。
  - 実装注記: 表現し直したテストは「約定価格・約定タイミング・件数（`closed_trades` の長さ）は不変／`trades[].shares`・`pnl` は変わってよい」を明示する。`closed` 要素（pnl を含む dict）の `==` 等価比較に戻さないこと。
  - 実装注記: キャップ縮退/観測テストの資本値は `synthetic_history` の価格レンジに依存する。テスト作成時に実際の合成価格で `bucket/fill` 境界を一度数値確認し、観測テスト（差が出る）と縮退テスト（キャップ）の資本を選定する。

### 6.5 フロント（`npm --prefix frontend test`）
- `AppSettings` 型に `account_size`/`risk_pct`、`PlanRow` 型に `shares`/`risk_amount` を追加。
- 既存の `rows.test.ts` は不変（サイジングは Top N ランキングに影響しない＝confidence 基準のまま）。表示の単体テストは PlanCard が純関数でないため、型追加とビルド通過＋既存13件グリーン維持を確認（必要なら金額整形の純関数を切り出してテスト）。

## 7. 将来拡張（このスコープでは作らない）
- **共有資金プール**（案3）: `_run_backtest_plan` を日付主体ループへ書き換え、全銘柄が単一口座を奪い合う厳密なポートフォリオ・サイジング。本打ち手のキャップ付きバケットを置換。
- **単元株（100株）丸め**: 日本株の発注現実に合わせた単元丸め・単元未満の警告。
- **動的 R:R**（打ち手9）と連動した損切り幅の動的化。
- **確信度で基準超の増額**（自信が高い時に基準%を超えてベット）＝v1 は保守的に縮小のみ。
- **ショートのサイジング**（ロング前提の解消後）。

## 8. リスクと後方互換
- **整数 score / direction・確信度は不変**: サイジングは別チャネル（`daily_plan` の新カラム・バックテストの株数のみ）。打ち手6/7 の確信度・ランキングは一切触らない。
- **バックテスト数値は変わる**: plan モードのサイジング変更により pnl/勝率/トレード数が変化する（これは意図した改善＝過大ベット抑制）。score モードと benchmark は不変。比較（戦略 vs ベンチマーク）は引き続き有効。
- **`risk_pct` はキーワード既定 `DEFAULT_RISK_PCT=1.0`**: 既存呼び出し（テスト含む）は引数追加なしで従来同等に動く（ただし plan モードはサイジング自体が変わるため数値は更新）。
- **旧 plan 行**: `shares`/`risk_amount` は NULL → UI は非表示（confidence=null と同じ防御）。
- **設定の防御**: `account_size`/`risk_pct` は範囲外で既定フォールバック（`_safe_top_n` と同方針）。
