import { describe, it, expect } from "vitest";
import { mergeRows, applyRefresh, selectTopN, riskSummary, earningsWarning, liquidityWarning, dataHealthWarnings, LIQUIDITY_MIN_YEN, Row, confidenceTier, planRationale, planRisks } from "@/lib/rows";
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
  it("株数・投資額・口座比率を計算する", () => {
    const r = riskSummary({ shares: 200, risk_amount: 10000, limit_price: 1000 }, 1_000_000);
    expect(r).not.toBeNull();
    expect(r!.shares).toBe(200);
    expect(r!.riskAmount).toBe(10000);
    expect(r!.positionValue).toBe(200000);     // 200 × 1000
    expect(r!.riskPctOfAccount).toBeCloseTo(1.0); // 10000 / 1,000,000
  });
  it("shares が無ければ null", () => {
    expect(riskSummary({ shares: null, risk_amount: null, limit_price: 1000 }, 1_000_000)).toBeNull();
  });
  it("limit_price が無ければ null（投資額0円の誤表示を防ぐ）", () => {
    expect(riskSummary({ shares: 200, risk_amount: 10000, limit_price: null }, 1_000_000)).toBeNull();
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

describe("earningsWarning", () => {
  it("しきい値以内は { days } を返す", () => {
    expect(earningsWarning(3)).toEqual({ days: 3 });
    expect(earningsWarning(0)).toEqual({ days: 0 });
    expect(earningsWarning(5)).toEqual({ days: 5 });       // 既定しきい値=5（境界含む）
    expect(earningsWarning(7, 7)).toEqual({ days: 7 });    // しきい値は引数で変更可（境界含む）
  });
  it("null・負・しきい値超は null", () => {
    expect(earningsWarning(null)).toBeNull();
    expect(earningsWarning(-1)).toBeNull();
    expect(earningsWarning(6)).toBeNull();
    expect(earningsWarning(8, 7)).toBeNull();              // しきい値超は引数変更後も null
  });
});

describe("liquidityWarning", () => {
  it("平均売買代金が閾値未満なら {turnover}", () => {
    expect(liquidityWarning(50_000_000)).toEqual({ turnover: 50_000_000 });
  });
  it("閾値以上は null", () => {
    expect(liquidityWarning(LIQUIDITY_MIN_YEN)).toBeNull();
    expect(liquidityWarning(500_000_000)).toBeNull();
  });
  it("null（不明・旧行）は警告しない", () => {
    expect(liquidityWarning(null)).toBeNull();
  });
  it("カスタムしきい値を使える", () => {
    expect(liquidityWarning(50_000_000, 40_000_000)).toBeNull();          // 50M >= 40M → null
    expect(liquidityWarning(30_000_000, 40_000_000)).toEqual({ turnover: 30_000_000 });
  });
});

describe("dataHealthWarnings", () => {
  it("各カウント>0 を文言化（順序: 出来高0→欠損→スパイク）", () => {
    const j = JSON.stringify({ zero_volume_days: 2, gap_days: 1, spike_days: 3 });
    expect(dataHealthWarnings(j)).toEqual([
      "出来高0の日が2日",
      "データ欠損 1件",
      "異常な値動き 3件（データ要確認）",
    ]);
  });
  it("全0は空配列", () => {
    expect(dataHealthWarnings(JSON.stringify({ zero_volume_days: 0, gap_days: 0, spike_days: 0 }))).toEqual([]);
  });
  it("null・壊れJSON・非オブジェクトは空配列", () => {
    expect(dataHealthWarnings(null)).toEqual([]);
    expect(dataHealthWarnings("{not json")).toEqual([]);
    expect(dataHealthWarnings("[]")).toEqual([]);   // valid JSON, wrong type
    expect(dataHealthWarnings("42")).toEqual([]);   // primitive
  });
});

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
  it("型ラベルの残り分岐（buy×flat / sell×down / sell×flat / sell×null）を固定", () => {
    const t = (direction: "buy" | "sell", weekly_trend: "up" | "down" | "flat" | null) =>
      planRationale({ direction, weekly_trend, regime: null, vol_ratio: null, confidence: null });
    expect(t("buy", "flat")).toBe("横ばいでの反発狙い。");
    expect(t("sell", "down")).toBe("下降トレンドの戻り売り。");
    expect(t("sell", "flat")).toBe("横ばいでの戻り売り。");
    expect(t("sell", null)).toBe("戻り売り。");
  });
  it("出来高は1.5倍ちょうどで後押しに入る（境界）", () => {
    const s = planRationale({
      direction: "buy", weekly_trend: "up", regime: null,
      vol_ratio: 1.5, confidence: null,
    });
    expect(s).toBe("上昇トレンドの押し目買い。出来高1.5倍が後押し。");
  });
  it("neutral は null", () => {
    expect(planRationale({
      direction: "neutral", weekly_trend: "up", regime: "risk_on",
      vol_ratio: 2, confidence: 80,
    })).toBeNull();
  });
});

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
  it("出来高0.7ちょうどは細り扱いにしない（境界）", () => {
    expect(planRisks(mk({ vol_ratio: 0.7 }), 1_000_000)).not.toContain("出来高が細い");
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

describe("selectTopN 薄商い除外", () => {
  const mk = (ticker: string, confidence: number, avg_turnover: number | null) =>
    ({ ticker, direction: "buy" as const, score: 3, confidence, avg_turnover });
  it("薄商い（閾値未満）を推奨から除外し、流動的は残す", () => {
    const rows = [mk("A", 80, 5_000_000), mk("B", 70, 500_000_000)];
    expect(selectTopN(rows, 3).map((r) => r.ticker)).toEqual(["B"]);
  });
  it("avg_turnover が null は除外しない（後方互換・confidence とは非対称）", () => {
    const rows = [mk("A", 80, null)];
    expect(selectTopN(rows, 3).map((r) => r.ticker)).toEqual(["A"]);
  });
});
