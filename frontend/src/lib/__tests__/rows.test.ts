import { describe, it, expect } from "vitest";
import { mergeRows, applyRefresh, selectTopN, riskSummary, Row } from "@/lib/rows";
import { RefreshRow, Signal, WatchItem } from "@/lib/api";

const watch: WatchItem[] = [
  { id: 1, ticker: "8306.T", name: "三菱UFJ", enabled: 1, created_at: "" },
  { id: 2, ticker: "7203.T", name: "トヨタ", enabled: 1, created_at: "" },
];

describe("mergeRows", () => {
  it("最新シグナル・現在値・出来高倍率・週足を銘柄に紐付ける", () => {
    const signals: Signal[] = [
      { id: 10, ticker: "8306.T", date: "2026-06-15", score: 2, direction: "buy",
        detail: { vol_ratio: 1.2, weekly_trend: "up" }, notified: 0 },
    ];
    const prices = { "8306.T": { date: "2026-06-15", close: 3250 } };
    const rows = mergeRows(watch, signals, prices);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      ticker: "8306.T", price: 3250, score: 2, direction: "buy",
      volRatio: 1.2, weeklyTrend: "up",
    });
    // シグナルが無い銘柄は null 埋め
    expect(rows[1]).toMatchObject({ ticker: "7203.T", price: null, score: null, direction: null });
  });

  it("同一銘柄では先頭（最新）のシグナルを採用する", () => {
    const signals: Signal[] = [
      { id: 20, ticker: "8306.T", date: "2026-06-15", score: 3, direction: "buy", detail: {}, notified: 0 },
      { id: 11, ticker: "8306.T", date: "2026-06-12", score: -2, direction: "sell", detail: {}, notified: 0 },
    ];
    const rows = mergeRows(watch, signals);
    expect(rows[0].score).toBe(3);
    expect(rows[0].direction).toBe("buy");
  });
});

const mk = (ticker: string, direction: any, score: number, confidence: number | null) =>
  ({ ticker, direction, score, confidence } as any);

describe("selectTopN", () => {
  it("confidence 降順で上位 N を返し neutral を除外する", () => {
    const rows = [
      mk("A.T", "buy", 2, 40),
      mk("B.T", "buy", 3, 80),
      mk("C.T", "neutral", 0, 99),  // neutral は対象外
      mk("D.T", "sell", -2, 60),
    ];
    const top = selectTopN(rows, 2);
    expect(top.map((r) => r.ticker)).toEqual(["B.T", "D.T"]);
  });

  it("同点は |score| 降順 → ticker 昇順で決定論的", () => {
    const rows = [
      mk("Z.T", "buy", 1, 70),
      mk("Y.T", "buy", 3, 70),
      mk("X.T", "buy", 3, 70),
    ];
    expect(selectTopN(rows, 3).map((r) => r.ticker)).toEqual(["X.T", "Y.T", "Z.T"]);
  });

  it("n<=0 は空、n>件数 は全件", () => {
    const rows = [mk("A.T", "buy", 2, 40), mk("B.T", "buy", 3, 80)];
    expect(selectTopN(rows, 0)).toEqual([]);
    expect(selectTopN(rows, 99).map((r) => r.ticker)).toEqual(["B.T", "A.T"]);
  });

  it("confidence が 0/null の actionable 行は推奨から除外する", () => {
    const rows = [
      mk("A.T", "buy", 2, null), // null（確信度なし）→ 除外
      mk("B.T", "buy", 1, 10), //  正 → 採用
      mk("E.T", "buy", 4, 0), //   0（方向はbuyだが連続確信度0＝落ちるナイフ）→ 除外
    ];
    expect(selectTopN(rows, 5).map((r) => r.ticker)).toEqual(["B.T"]);
  });
});

describe("riskSummary", () => {
  it("株数・投資額・口座%を整形する", () => {
    const r = riskSummary({ shares: 200, risk_amount: 10000, limit_price: 1000 }, 1_000_000);
    expect(r).not.toBeNull();
    expect(r!.shares).toBe(200);
    expect(r!.positionValue).toBe(200000);     // 200 × 1000
    expect(r!.riskPctOfAccount).toBeCloseTo(1.0); // 10000 / 1,000,000
  });
  it("shares が無ければ null", () => {
    expect(riskSummary({ shares: null, risk_amount: null, limit_price: 1000 }, 1_000_000)).toBeNull();
  });
});

describe("applyRefresh", () => {
  it("更新分だけ上書きし、対象外の行はそのまま残す", () => {
    const base: Row[] = [
      { id: 1, ticker: "8306.T", name: "三菱UFJ", price: 100, score: 0, direction: "neutral", date: "old", volRatio: null, weeklyTrend: null },
      { id: 2, ticker: "7203.T", name: "トヨタ", price: 200, score: 1, direction: "neutral", date: "old", volRatio: 0.9, weeklyTrend: "flat" },
    ];
    const updated: RefreshRow[] = [
      { id: 99, ticker: "8306.T", date: "2026-06-16", price: 3260, score: 2, direction: "buy",
        detail: { vol_ratio: 1.5, weekly_trend: "up" } },
    ];
    const out = applyRefresh(base, updated);
    expect(out[0]).toMatchObject({ price: 3260, score: 2, direction: "buy", volRatio: 1.5, weeklyTrend: "up" });
    expect(out[1]).toBe(base[1]); // 未更新は同一参照
  });
});
