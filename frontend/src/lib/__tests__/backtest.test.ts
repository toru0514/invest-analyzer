import { describe, it, expect } from "vitest";
import { buildBacktestBody } from "@/lib/backtest";

const base = { capital: 3000, days: 22, demo: true, persist: false,
               atrExit: true, trailAtrMult: 0, maxHoldDays: 0 };

describe("buildBacktestBody", () => {
  it("trail/time が 0 ならペイロードに含めない（OFF）", () => {
    const b = buildBacktestBody(base);
    expect(b.exit_mode).toBe("plan");
    expect(b.trail_atr_mult).toBeUndefined();
    expect(b.max_hold_days).toBeUndefined();
  });
  it("trail/time が正なら貫通する", () => {
    const b = buildBacktestBody({ ...base, trailAtrMult: 3, maxHoldDays: 10 });
    expect(b.trail_atr_mult).toBe(3);
    expect(b.max_hold_days).toBe(10);
  });
  it("atrExit=false は score モード", () => {
    expect(buildBacktestBody({ ...base, atrExit: false }).exit_mode).toBe("score");
  });
});
