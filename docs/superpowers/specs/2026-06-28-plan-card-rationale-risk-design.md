# 打ち手13: 作戦カードの確信度＋根拠文＋リスク（課題10）— 設計

- 日付: 2026-06-28
- フェーズ: D（守備オーバーレイの磨き込み）— 最後の打ち手
- 対応課題: 課題10「『作戦』としての説明力・確信度が弱い」
- 種別: 説明力（表示層）のみ。score / direction / confidence / build_plan / backtest は一切変更しない（加法的・後方互換）。verify-before-adopt 診断は不要。

---

## 1. 目的

作戦カードの根拠が現状「5日線◯◯ / ATR押し目◯◯ / サポート◯◯（方式: atr）」という数値の羅列で、
「なぜ買うか・どれくらい自信があるか・何がリスクか」が人間の言葉になっていない。
これを課題10 の例：

> 「順張りの押し目買い。週足↑・地合い良好・出来高1.8倍が後押し。確信度は中程度。
>  リスク：3日後に決算・損切り到達で約−¥12,000（口座の1.2%）」

のような ルールベースの自然文（根拠文＋リスク） にする。LLM は不要（課題14 とは別）。

---

## 2. 棲み分け（既存の説明レイヤーとの関係）— 設計の核心

カードには既に説明系の要素が3つ存在する。打ち手13 はそれらを置き換えず、役割を整理して埋める。

| 既存要素 | 出どころ | 打ち手13 での扱い |
|---|---|---|
| 確信度バッジ（青 0-100） | 打ち手6 `detail["confidence"]`（量的・常時） | 不変。根拠文は確信度を再計算せず、量を言葉に翻訳する（高め/中程度/低め）。バッジが数値、根拠文が語感。 |
| terse「根拠:」文 | `build_plan` の指値算出メモ | 降格：「指値の根拠」フットノートに移す（指値の出どころは発注時の実用情報ゆえ捨てない＝拡張であって置換でない）。 |
| AI解説ボックス（藍） | Gemini `ai_summary/ai_confidence/ai_risks`（best-effort・キー無しで不在） | 不変。常時表示の根拠文（決定論）＋任意のAI解説（LLM）で役割分担。ラベルで区別済み（「根拠」vs「AI解説」・「確信度」vs「AI確信度」）。 |

加えてカードには ⚠決算（打ち手10）・⚠薄商い（打ち手12）・データ注意（打ち手12）・出来高倍率・週足・推奨株数/想定損失（打ち手8） のバッジ／行がある。

整合の担保方針：リスク文はこれら既存のフロント純関数を再利用して生成する
（`earningsWarning` / `liquidityWarning` / `dataHealthWarnings` / `riskSummary`）。
同じ生値・同じしきい値を通すので、バッジとリスク文が食い違うことが構造的に起きない。

---

## 3. アプローチ（検討した3案）

### 案1（採用）: フロント純関数で生成
- `rows.ts` に純関数 `planRationale(row)` / `planRisks(row, accountSize)` を追加。
- 既存の警告ヘルパ（earnings/liquidity/dataHealth/riskSummary）を再利用。
- データは既に揃っている：`db.list_plan` は `SELECT * FROM daily_plan` なので
  `regime` 列（打ち手11 で永続化済み）は既に GET /plan で配信されている。
  PlanRow 型に項目が無いだけ。→ バックエンド変更ゼロ・DBマイグレーションゼロ。
- 型ラベル（順張り/逆張り）は `direction × weekly_trend` の2軸で決める。
  `regime` は型ラベルには使わず、後押し要因（地合い良好/中立）とリスク（リスクオフ警戒）に寄与する
  （厳密な配線は §4.2 が正・本節は概略）。

### 案2（不採用）: バックエンド生成＋daily_plan 列追加
- `build_plan` または新ヘルパで根拠文/リスク文を生成し新列に永続化。`_groups` で型を厳密化できる。
- 却下理由：DBマイグレーションが要る／リスク判定を再実装するとフロントのバッジ（earnings/liquidity 等）と
  しきい値が二重管理→乖離リスク／「戦略不変・表示層のみ」の打ち手に対し重い。

### 案3（不採用）: バックエンドが構造化 reason-code を出し、フロントが描画
- `detail["_reasons"]` のような機械可読 factor を backend が出し frontend が文に。
- 却下理由：★低難度の表示打ち手に対し新しい契約を増やしすぎ（over-engineering）。

採用＝案1。打ち手12 のイディオム（生値を保存し、フロント純関数で表示文を組む）を完全踏襲し、
既存ヘルパ再利用でバッジとの整合を無料で得る。`_groups` 露出による型の厳密化は将来フォローに残す。

---

## 4. コンポーネント設計（すべてフロント）

### 4.1 `api.ts` — PlanRow に regime を追加

```ts
export type PlanRow = {
  // …既存…
  regime: "risk_on" | "neutral" | "risk_off" | null;  // 打ち手11で永続化済み・/plan は SELECT * で既に配信
  // …既存…
};
```

値は既に JSON に含まれている（`SELECT *`）。型に載せて使えるようにするだけ。API・DB は無改変。

### 4.2 `rows.ts` — 純関数2つ＋ラベル定数

(a) `planRationale(row): string | null`
actionable（buy/sell）のときに「型＋後押し要因」を1文で返す。neutral / 必要情報欠如は `null`。

構成要素：
- 型ラベル（`direction × weekly_trend`）:
  - buy×up→「上昇トレンドの押し目買い」 / buy×flat→「横ばいでの反発狙い」 /
    buy×down→「下落局面での逆張り（反発狙い）」 / buy×null→「押し目買い」
  - sell×down→「下降トレンドの戻り売り」 / sell×up→「上昇中の戻り売り（逆張り）」 /
    sell×flat→「横ばいでの戻り売り」 / sell×null→「戻り売り」
- 後押し要因（該当するものを「・」で連結）:
  - 地合い（regime）: risk_on→「地合い良好」 / neutral→「地合い中立」 / risk_off は後押しに入れない（リスク側へ）
  - 週足整合: 型ラベルに含意されるため二重に出さない
  - 出来高: `vol_ratio >= 1.5` → 「出来高{vol_ratio を小数1桁}倍が後押し」
    （例: 1.8倍。ヘッダのバッジは `.toFixed(2)`＝1.80倍だが、根拠文は読み物なので1桁に丸める。
    `vol_ratio.toFixed(1)` でなく**末尾0を残さない** `Number(vol_ratio.toFixed(1))` 表記＝1.8/2.0→"2"。
    テストは1.8倍を固定するので実装は1桁丸めで一意）
  - 確信度の語感: tier（下記 `confidenceTier`）→「確信度は高め/中程度/低め」
- 文末は「。」。後押し要因が無ければ型ラベル＋確信度語感のみ。

(b) `planRisks(row, accountSize): string[]`
actionable のときにリスク文の配列を返す（空配列可）。既存ヘルパを再利用：
- 週足逆行: (buy×weekly down) or (sell×weekly up) → 「週足が逆行（down/up）」
- 地合い警戒: regime==risk_off → 「地合いが弱い（リスクオフ）」
- 確信度が低い: `confidenceTier(confidence)==="low"` → 「確信度が低め」
- 出来高細り: `vol_ratio != null && vol_ratio < 0.7` → 「出来高が細い」
- 決算近接: `earningsWarning(days_to_earnings)` → 「{days}日後に決算」
- 薄商い: `liquidityWarning(avg_turnover)` → 「薄商い（約定しづらい）」
- データ異常: `dataHealthWarnings(data_health)` の各文字列をそのまま展開
- 損切りダウンサイド: `riskSummary(row, accountSize)` が非nullなら
  → 「損切り到達で約 −¥{riskAmount}（口座の{riskPctOfAccount}%）」

順序は「戦略リスク（週足/地合い/確信度/出来高）→ イベント（決算/薄商い/データ）→ 金額」。

(c) `confidenceTier(confidence: number | null): "high" | "mid" | "low" | null`
しきい値（フロント定数）: `>= CONF_HIGH(60)`→high / `>= CONF_MID(35)`→mid / それ未満→low / null→null。
※ 打ち手6 の確信度は低位圧縮（診断メモ：50-75 は稀・75+ ゼロ）。tier しきい値はこの分布に合わせ
   `high=60 / mid=35` とする（将来、実績に応じ調整可＝フロント定数なので再生成不要）。

### 4.3 `plan/page.tsx` — カードの表示変更（actionable 枝のみ）

現状（actionable 枝・抜粋）:

```
[提案指値][利確][損切]            ← PlanMetric 3つ（不変）
推奨株数 … ／ 想定損失 …（口座の%）  ← riskSummary（不変）
根拠: 5日線… / ATR押し目… / サポート…（方式: atr）  ← terse（降格対象）
[AI解説ボックス]                  ← 不変
```

変更後:

```
[提案指値][利確][損切]            ← 不変
推奨株数 … ／ 想定損失 …（口座の%）  ← 不変
根拠: {planRationale(row)}        ← ★新（主役・slate-700 で少し強調）
リスク: {planRisks(row,acct).join("・")}  ← ★新（amber-700・空なら非表示）
指値の根拠: 5日線… / ATR押し目…（方式: atr）  ← terse 降格（text-[10px] gray フットノート）
[AI解説ボックス]                  ← 不変
```

- neutral / 保有者向け枝・未生成枝は現状のまま（根拠文/リスク文は actionable 限定）。
- 確信度バッジ（青・ヘッダ）は不変。根拠文の「確信度は中程度」と数値バッジが補完関係。

---

## 5. データフロー（無改変であることの確認）

```
perform_refresh → build_plan(terse rationale) ┐
evaluate → detail["confidence"], regime        ├→ upsert_plan → daily_plan（regime 既に保存）
                                               ┘
GET /plan → db.list_plan → SELECT *（regime 含む）→ PlanRow(JSON)
                                               ↓
plan/page.tsx: planRationale / planRisks / confidenceTier（純関数・新）
             + earningsWarning/liquidityWarning/dataHealthWarnings/riskSummary（既存・再利用）
```

backend（Python）・DB スキーマ・API レスポンス本体は1行も変えない。
変更は `api.ts`（型）・`rows.ts`（純関数）・`plan/page.tsx`（描画）の3ファイルのみ。

---

## 6. エラー処理・エッジケース

- `planRationale`：neutral / direction 欠如 → `null`（カードは現状の中立文を表示）。
- `weekly_trend` / `regime` / `vol_ratio` / `confidence` が null でも部分情報で文を組む
  （例：regime 不明なら地合い句を省く）。例外は投げない（既存ヘルパと同契約）。
- `planRisks`：該当なし → `[]`（カードはリスク行を非表示）。
- `riskSummary` が null（旧行/非buy/指値欠如）→ 金額リスクは省く（他リスクは出す）。
- `dataHealthWarnings` は壊れ JSON/null を `[]` で吸収（既存実装）。
- regime の未知文字列 → 地合い句・地合い警戒とも出さない（安全フォールバック）。

---

## 7. テスト（vitest・`frontend/src/lib/__tests__/rows.test.ts` に追加）

純関数なので決定論的にユニットテストする。

- `confidenceTier`: 境界（59/60、34/35）・null。
- `planRationale`:
  - buy×up×risk_on×vol1.8×conf high → 「上昇トレンドの押し目買い。地合い良好・出来高1.8倍が後押し。確信度は高め。」
  - buy×down×risk_off → 型が逆張り、後押しに地合いを入れない。
  - sell×up → 「上昇中の戻り売り（逆張り）」。
  - neutral → null。weekly_trend=null → 「押し目買い」。
- `planRisks`:
  - buy×weekly down → 「週足が逆行（down）」を含む。
  - regime risk_off → 「地合いが弱い（リスクオフ）」。
  - days_to_earnings=3 → 「3日後に決算」（earningsWarning 経由）。
  - avg_turnover 薄 → 「薄商い…」（liquidityWarning 経由）。
  - data_health に zero_volume → dataHealthWarnings の文字列が出る。
  - riskSummary 成立 → 「損切り到達で約 −¥…（口座の…%）」。
  - 何も該当しない健全 buy → `[]`。
  - 順序：戦略→イベント→金額 の固定順。
- 既存テスト（selectTopN/riskSummary/earnings/liquidity/dataHealth）は不変で緑のまま。

---

## 8. スコープ外（将来フォロー・非ブロッカー）

- `_groups`/`_strengths`/`rs` をフロントへ露出して型を厳密化（MACD好転・OBV増・相対力など個別ドライバ）。
  今回は direction×週足×regime の近似で十分（課題10 の例の粒度）。
- 確信度 tier しきい値の設定UI化（当面フロント定数）。
- AI解説と根拠文の統合表示（重複感が出たら整理）。今回は役割分担で併存。
- neutral / 保有者向け枝への根拠文（今回は actionable 限定）。
- 根拠文の i18n / 語彙チューニング。

---

## 9. 受け入れ基準

- buy/sell カードに自然文の根拠と（該当時）リスク行が出る。
- 根拠文の確信度語感が確信度バッジ（数値）と矛盾しない（同一 `confidence` 由来）。
- リスク行の項目が既存バッジ（決算/薄商い/データ）と矛盾しない（同一ヘルパ由来）。
- terse文は「指値の根拠」フットノートとして残る（情報を失わない）。
- backend / DB / API 本体は無改変。`npm --prefix frontend test` 緑（既存＋新規）。
- backend テスト（`pytest backend/ -q`）は無改変で緑のまま（変更が無いことの確認）。
