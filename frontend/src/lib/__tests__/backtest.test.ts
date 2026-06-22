import { describe, it, expect } from "vitest";
import { buildBacktestBody } from "@/lib/backtest";

const base = { capital: 3000, days: 22, demo: true, persist: false,
               atrExit: true, trailAtrMult: 0, maxHoldDays: 0,
               earningsAware: false, earningsExitDays: 0 };

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
  it("earningsAware=false なら earnings 系をペイロードに含めない", () => {
    const b = buildBacktestBody({ ...base, earningsAware: false, earningsExitDays: 1 });
    expect(b.earnings_aware).toBeUndefined();
    expect(b.earnings_exit_days).toBeUndefined();
  });
  it("earningsAware=true で付与・exit_days は >0 のときのみ付与", () => {
    const b1 = buildBacktestBody({ ...base, earningsAware: true, earningsExitDays: 0 });
    expect(b1.earnings_aware).toBe(true);
    expect(b1.earnings_exit_days).toBeUndefined();
    const b2 = buildBacktestBody({ ...base, earningsAware: true, earningsExitDays: 2 });
    expect(b2.earnings_aware).toBe(true);
    expect(b2.earnings_exit_days).toBe(2);
  });
});
