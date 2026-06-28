# 打ち手13: 作戦カードの確信度＋根拠文＋リスク — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 作戦カードに、ルールベースの自然文「根拠」と「リスク」を追加し、既存の terse 文を「指値の根拠」フットノートに降格する（表示層のみ・戦略不変）。

**Architecture:** 完全フロントエンド。`rows.ts` に純関数 `confidenceTier` / `planRationale` / `planRisks` を追加し、既存の警告ヘルパ（`earningsWarning`/`liquidityWarning`/`dataHealthWarnings`/`riskSummary`）を再利用。`regime` は既に GET /plan（`SELECT *`）で配信済みなので `PlanRow` 型に追加するだけ（backend/DB/API は無改変）。`plan/page.tsx` の actionable 枝で描画。

**Tech Stack:** TypeScript / React (Next.js App Router) / vitest。設計: `docs/superpowers/specs/2026-06-28-plan-card-rationale-risk-design.md`。

**検証コマンド:**
- 単体テスト: `npm --prefix frontend test`
- 型チェック: `cd frontend && npx tsc --noEmit`
- backend 無改変の確認: `backend/venv/bin/python -m pytest backend/ -q`（緑のまま）

---

## File Structure

- Modify: `frontend/src/lib/rows.ts` — 純関数3つ＋定数 `CONF_HIGH`/`CONF_MID` を追加（既存ヘルパの下に配置）
- Test: `frontend/src/lib/__tests__/rows.test.ts` — 3つの describe を追加
- Modify: `frontend/src/lib/api.ts` — `PlanRow` 型に `regime` を追加（1行）
- Modify: `frontend/src/app/plan/page.tsx` — import 追加＋ actionable 枝の描画変更

各タスクは前タスクに積み上がる。Task 1→2→3 は純関数 TDD、Task 4 は型、Task 5 は描画（型チェック＋ビルドで検証）。

---

## Task 1: `confidenceTier`（確信度を語感ティアへ）

**Files:**
- Modify: `frontend/src/lib/rows.ts`
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/lib/__tests__/rows.test.ts` の import 行に `confidenceTier` を足し、末尾に describe を追加:

```ts
// import 行（既存）に confidenceTier を追加:
// import { mergeRows, applyRefresh, selectTopN, riskSummary, earningsWarning,
//   liquidityWarning, dataHealthWarnings, confidenceTier, LIQUIDITY_MIN_YEN, Row } from "@/lib/rows";

describe("confidenceTier", () => {
  it("しきい値で high/mid/low に分ける（境界は以上）", () => {
    expect(confidenceTier(60)).toBe("high");
    expect(confidenceTier(100)).toBe("high");
    expect(confidenceTier(59)).toBe("mid");
    expect(confidenceTier(35)).toBe("mid");
    expect(confidenceTier(34)).toBe("low");
    expect(confidenceTier(0)).toBe("low");
  });
  it("null は null", () => {
    expect(confidenceTier(null)).toBeNull();
  });
});
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `npm --prefix frontend test`
Expected: FAIL（`confidenceTier` is not exported / not a function）

- [ ] **Step 3: 最小実装**

`frontend/src/lib/rows.ts` の `EARNINGS_WARN_DAYS` 定義の近く（既存の警告ヘルパ群の末尾、ファイル下部）に追加:

```ts
/** 確信度（打ち手6・0-100）の語感ティア。バッジが数値、根拠文が語感で補完する。
 *  打ち手6 の confidence は低位圧縮分布のため high=60 / mid=35（フロント定数・再生成不要で調整可）。 */
export const CONF_HIGH = 60;
export const CONF_MID = 35;

export function confidenceTier(
  confidence: number | null,
): "high" | "mid" | "low" | null {
  if (confidence == null) return null;
  if (confidence >= CONF_HIGH) return "high";
  if (confidence >= CONF_MID) return "mid";
  return "low";
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `npm --prefix frontend test`
Expected: PASS（既存テストも緑のまま）

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: confidenceTier（確信度の語感ティア・打ち手13）"
```

---

## Task 2: `planRationale`（自然文の根拠）

**Files:**
- Modify: `frontend/src/lib/rows.ts`
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを書く**

import 行に `planRationale` を追加し、describe を追加:

```ts
describe("planRationale", () => {
  it("buy×週足up×risk_on×出来高1.8×高確信 → 型＋後押し＋確信度語感", () => {
    const s = planRationale({
      direction: "buy", weekly_trend: "up", regime: "risk_on",
      vol_ratio: 1.8, confidence: 72,
    });
    expect(s).toBe("上昇トレンドの押し目買い。地合い良好・出来高1.8倍が後押し。確信度は高め。");
  });
  it("buy×週足down×risk_off → 逆張り型・後押しに地合いを入れない", () => {
    const s = planRationale({
      direction: "buy", weekly_trend: "down", regime: "risk_off",
      vol_ratio: 1.0, confidence: 50,
    });
    expect(s).toBe("下落局面での逆張り（反発狙い）。確信度は中程度。");
  });
  it("sell×週足up → 上昇中の戻り売り（逆張り）", () => {
    const s = planRationale({
      direction: "sell", weekly_trend: "up", regime: "neutral",
      vol_ratio: null, confidence: 20,
    });
    expect(s).toBe("上昇中の戻り売り（逆張り）。地合い中立。確信度は低め。");
  });
  it("週足 null は型ラベルを汎用名に", () => {
    const s = planRationale({
      direction: "buy", weekly_trend: null, regime: null,
      vol_ratio: null, confidence: null,
    });
    expect(s).toBe("押し目買い。");
  });
  it("neutral は null", () => {
    expect(planRationale({
      direction: "neutral", weekly_trend: "up", regime: "risk_on",
      vol_ratio: 2, confidence: 80,
    })).toBeNull();
  });
});
```

注: 4例目は drivers 空・conf 空 → 「押し目買い。」のみ（parts=["押し目買い"] → join → +"。"）。

- [ ] **Step 2: テストが失敗することを確認**

Run: `npm --prefix frontend test`
Expected: FAIL（`planRationale` is not a function）

- [ ] **Step 3: 最小実装**

`confidenceTier` の下に追加（`Direction` は既にファイル先頭で import 済み）:

```ts
const TIER_WORD: Record<"high" | "mid" | "low", string> = {
  high: "高め", mid: "中程度", low: "低め",
};

/** 型ラベル（順張り/逆張り）。direction × weekly_trend の2軸で決める（regime は使わない）。 */
function planTypeLabel(direction: Direction, weekly: string | null): string {
  if (direction === "buy") {
    if (weekly === "up") return "上昇トレンドの押し目買い";
    if (weekly === "flat") return "横ばいでの反発狙い";
    if (weekly === "down") return "下落局面での逆張り（反発狙い）";
    return "押し目買い";
  }
  if (weekly === "down") return "下降トレンドの戻り売り";
  if (weekly === "up") return "上昇中の戻り売り（逆張り）";
  if (weekly === "flat") return "横ばいでの戻り売り";
  return "戻り売り";
}

/** 作戦カードの自然文「根拠」。actionable（buy/sell）のみ。neutral は null。
 *  型ラベル＋後押し要因（地合い・出来高）＋確信度の語感を1文に。確信度は打ち手6 を再利用（再計算しない）。 */
export function planRationale(row: {
  direction: Direction;
  weekly_trend: "up" | "down" | "flat" | null;
  regime: "risk_on" | "neutral" | "risk_off" | null;
  vol_ratio: number | null;
  confidence: number | null;
}): string | null {
  if (row.direction === "neutral") return null;
  const drivers: string[] = [];
  if (row.regime === "risk_on") drivers.push("地合い良好");
  else if (row.regime === "neutral") drivers.push("地合い中立");
  if (row.vol_ratio != null && row.vol_ratio >= 1.5) {
    drivers.push(`出来高${Number(row.vol_ratio.toFixed(1))}倍が後押し`);
  }
  const tier = confidenceTier(row.confidence);
  const parts = [planTypeLabel(row.direction, row.weekly_trend)];
  if (drivers.length) parts.push(drivers.join("・"));
  if (tier) parts.push(`確信度は${TIER_WORD[tier]}`);
  return parts.join("。") + "。";
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `npm --prefix frontend test`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: planRationale 自然文の根拠（打ち手13）"
```

---

## Task 3: `planRisks`（リスク文・既存ヘルパ再利用）

**Files:**
- Modify: `frontend/src/lib/rows.ts`
- Test: `frontend/src/lib/__tests__/rows.test.ts`

- [ ] **Step 1: 失敗するテストを書く**

import 行に `planRisks` を追加。テスト DRY 化のため factory を使う:

```ts
describe("planRisks", () => {
  type RiskRow = Parameters<typeof planRisks>[0];
  const base: RiskRow = {
    direction: "buy", weekly_trend: "up", regime: "risk_on",
    vol_ratio: 1.2, confidence: 70,
    days_to_earnings: null, avg_turnover: 500_000_000, data_health: null,
    shares: null, risk_amount: null, limit_price: null,
  };
  const mk = (o: Partial<RiskRow>): RiskRow => ({ ...base, ...o });

  it("健全な buy はリスク無し（空配列）", () => {
    expect(planRisks(base, 1_000_000)).toEqual([]);
  });
  it("neutral は空配列", () => {
    expect(planRisks(mk({ direction: "neutral" }), 1_000_000)).toEqual([]);
  });
  it("週足逆行（buy×down）を出す", () => {
    expect(planRisks(mk({ weekly_trend: "down" }), 1_000_000)).toContain("週足が逆行（down）");
  });
  it("地合いリスクオフを出す", () => {
    expect(planRisks(mk({ regime: "risk_off" }), 1_000_000)).toContain("地合いが弱い（リスクオフ）");
  });
  it("低確信を出す", () => {
    expect(planRisks(mk({ confidence: 20 }), 1_000_000)).toContain("確信度が低め");
  });
  it("出来高細りを出す", () => {
    expect(planRisks(mk({ vol_ratio: 0.5 }), 1_000_000)).toContain("出来高が細い");
  });
  it("決算近接（earningsWarning 経由）", () => {
    expect(planRisks(mk({ days_to_earnings: 3 }), 1_000_000)).toContain("3日後に決算");
  });
  it("薄商い（liquidityWarning 経由）", () => {
    expect(planRisks(mk({ avg_turnover: 50_000_000 }), 1_000_000)).toContain("薄商い（約定しづらい）");
  });
  it("データ異常（dataHealthWarnings 経由）", () => {
    const r = planRisks(mk({ data_health: JSON.stringify({ zero_volume_days: 2 }) }), 1_000_000);
    expect(r).toContain("出来高0の日が2日");
  });
  it("損切りダウンサイド（riskSummary 経由・金額と口座%）", () => {
    const r = planRisks(mk({ shares: 100, risk_amount: 12_000, limit_price: 3000 }), 1_000_000);
    expect(r).toContain("損切り到達で約 −¥12,000（口座の1.2%）");
  });
  it("複合時の順序＝戦略→イベント→金額", () => {
    const r = planRisks(mk({
      weekly_trend: "down", regime: "risk_off", confidence: 20, vol_ratio: 0.5,
      days_to_earnings: 3, avg_turnover: 50_000_000,
      data_health: JSON.stringify({ zero_volume_days: 2 }),
      shares: 100, risk_amount: 12_000, limit_price: 3000,
    }), 1_000_000);
    expect(r).toEqual([
      "週足が逆行（down）",
      "地合いが弱い（リスクオフ）",
      "確信度が低め",
      "出来高が細い",
      "3日後に決算",
      "薄商い（約定しづらい）",
      "出来高0の日が2日",
      "損切り到達で約 −¥12,000（口座の1.2%）",
    ]);
  });
});
```

注: `−¥` の「−」は U+2212（実装と同一文字）。`riskPctOfAccount = 12000/1000000*100 = 1.2` → `toFixed(1)`→"1.2"。

- [ ] **Step 2: テストが失敗することを確認**

Run: `npm --prefix frontend test`
Expected: FAIL（`planRisks` is not a function）

- [ ] **Step 3: 最小実装**

`planRationale` の下に追加（同ファイル内の `earningsWarning`/`liquidityWarning`/`dataHealthWarnings`/`riskSummary` を再利用）:

```ts
/** 作戦カードの「リスク」文配列。actionable のみ。既存の警告ヘルパを再利用し、
 *  バッジ（決算/薄商い/データ）と同一ソース・同一しきい値で整合を保つ。
 *  順序: 戦略リスク（週足/地合い/確信度/出来高）→ イベント（決算/薄商い/データ）→ 金額。 */
export function planRisks(
  row: {
    direction: Direction;
    weekly_trend: "up" | "down" | "flat" | null;
    regime: "risk_on" | "neutral" | "risk_off" | null;
    vol_ratio: number | null;
    confidence: number | null;
    days_to_earnings: number | null;
    avg_turnover: number | null;
    data_health: string | null;
    shares: number | null;
    risk_amount: number | null;
    limit_price: number | null;
  },
  accountSize: number,
): string[] {
  if (row.direction === "neutral") return [];
  const out: string[] = [];
  // 戦略リスク
  if (
    (row.direction === "buy" && row.weekly_trend === "down") ||
    (row.direction === "sell" && row.weekly_trend === "up")
  ) {
    out.push(`週足が逆行（${row.weekly_trend}）`);
  }
  if (row.regime === "risk_off") out.push("地合いが弱い（リスクオフ）");
  if (confidenceTier(row.confidence) === "low") out.push("確信度が低め");
  if (row.vol_ratio != null && row.vol_ratio < 0.7) out.push("出来高が細い");
  // イベント（既存ヘルパ再利用）
  const ew = earningsWarning(row.days_to_earnings);
  if (ew) out.push(`${ew.days}日後に決算`);
  if (liquidityWarning(row.avg_turnover)) out.push("薄商い（約定しづらい）");
  out.push(...dataHealthWarnings(row.data_health));
  // 金額（riskSummary 再利用）
  const rs = riskSummary(row, accountSize);
  if (rs) {
    out.push(
      `損切り到達で約 −¥${Math.round(rs.riskAmount).toLocaleString()}（口座の${rs.riskPctOfAccount.toFixed(1)}%）`,
    );
  }
  return out;
}
```

- [ ] **Step 4: テストが通ることを確認**

Run: `npm --prefix frontend test`
Expected: PASS（既存テスト＋ Task1/2/3 すべて緑）

- [ ] **Step 5: コミット**

```bash
git add frontend/src/lib/rows.ts frontend/src/lib/__tests__/rows.test.ts
git commit -m "feat: planRisks リスク文（既存ヘルパ再利用・打ち手13）"
```

---

## Task 4: `PlanRow` 型に `regime` を追加

**Files:**
- Modify: `frontend/src/lib/api.ts`

backend/DB/API は無改変。`regime` は既に `SELECT *` で JSON に含まれている。型に載せて `page.tsx` から渡せるようにするだけ。

- [ ] **Step 1: 型を追加**

`frontend/src/lib/api.ts` の `PlanRow` 型、`confidence: number | null;` の直後に1行追加:

```ts
  confidence: number | null;
  regime: "risk_on" | "neutral" | "risk_off" | null;  // 打ち手11で永続化済み・/plan は SELECT * で配信
```

- [ ] **Step 2: 型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラー無し（既存コードは regime を未使用なので型追加だけでは壊れない）

- [ ] **Step 3: コミット**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat: PlanRow に regime（/plan で既配信・打ち手13）"
```

---

## Task 5: `plan/page.tsx` で根拠文＋リスク文を描画・terse を降格

**Files:**
- Modify: `frontend/src/app/plan/page.tsx`

- [ ] **Step 1: import に純関数を追加**

`frontend/src/app/plan/page.tsx` の rows import（現状）:

```ts
import { dataHealthWarnings, earningsWarning, liquidityWarning, riskSummary, selectTopN } from "@/lib/rows";
```

を次に変更:

```ts
import { dataHealthWarnings, earningsWarning, liquidityWarning, planRationale, planRisks, riskSummary, selectTopN } from "@/lib/rows";
```

- [ ] **Step 2: actionable 枝の terse 行を置き換える**

`PlanCard` の actionable 枝（`{row!.rationale && <p className="mt-2 text-xs text-slate-500">根拠: {row!.rationale}</p>}`）を次に置換:

```tsx
{(() => {
  const r = planRationale(row!);
  return r ? <p className="mt-2 text-xs font-medium text-slate-700">根拠: {r}</p> : null;
})()}
{(() => {
  const risks = planRisks(row!, accountSize);
  return risks.length ? (
    <p className="mt-1 text-xs text-amber-700">リスク: {risks.join("・")}</p>
  ) : null;
})()}
{row!.rationale && (
  <p className="mt-1 text-[10px] text-slate-400">指値の根拠: {row!.rationale}</p>
)}
```

（根拠文＝主役 slate-700 / リスク＝amber-700 / terse＝降格 10px gray「指値の根拠」。`accountSize` は既に PlanCard の prop。）

- [ ] **Step 3: 型チェック**

Run: `cd frontend && npx tsc --noEmit`
Expected: エラー無し（`planRationale(row!)` は PlanRow に regime があるので通る）

- [ ] **Step 4: テスト＋ビルドで最終確認**

Run: `npm --prefix frontend test`
Expected: PASS（全件緑）

Run: `npm --prefix frontend run build`
Expected: Next ビルド成功（型エラー無し）

- [ ] **Step 5: コミット**

```bash
git add frontend/src/app/plan/page.tsx
git commit -m "feat: 作戦カードに根拠文＋リスク・terseを指値の根拠に降格（打ち手13・フロント）"
```

---

## Task 6: backend 無改変の確認＋全体グリーン

**Files:** なし（検証のみ）

- [ ] **Step 1: backend テストが無改変で緑であることを確認**

Run: `backend/venv/bin/python -m pytest backend/ -q`
Expected: PASS（変更していないので件数・結果とも従来どおり）

- [ ] **Step 2: frontend 単体テスト最終確認**

Run: `npm --prefix frontend test`
Expected: PASS（既存＋新規）

- [ ] **Step 3（必要なら）: 受け入れ確認メモ**

設計 §9 の受け入れ基準を満たすことを目視確認（コミット不要）:
- buy/sell カードに自然文の根拠と（該当時）リスク行が出る
- 根拠文の確信度語感がバッジ（数値）と同一 confidence 由来
- リスク行が既存バッジ（決算/薄商い/データ）と矛盾しない（同一ヘルパ由来）
- terse 文が「指値の根拠」フットノートとして残る
- backend/DB/API 無改変

---

## 完了後

- final code-review（superpowers:requesting-code-review）
- finishing-a-development-branch（main へ fast-forward マージ＋`git push origin main`）
- メモリ `roadmap-progress.md` を更新（打ち手13 採用＝フェーズD 完了）
