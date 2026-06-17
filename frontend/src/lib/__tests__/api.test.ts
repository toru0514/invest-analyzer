import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "@/lib/api";

function mockFetch(body: unknown, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  });
}

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch([]));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getWatchlist は /watchlist を呼ぶ", async () => {
    const f = mockFetch([{ id: 1, ticker: "8306.T", name: "x", enabled: 1, created_at: "" }]);
    vi.stubGlobal("fetch", f);
    const res = await api.getWatchlist();
    expect(f).toHaveBeenCalledWith("http://localhost:8000/watchlist", expect.any(Object));
    expect(res[0].ticker).toBe("8306.T");
  });

  it("optimize は POST /optimize にボディを送る", async () => {
    const f = mockFetch({
      chosen_params: { threshold: 2, exit_mode: "plan" },
      in_sample: { sample: "in_sample", sweep: [], best: null, baseline_pnl_pct: 0,
                   contributions: [], pnl_pct: 0, expectancy: null, trade_count: 0, win_rate: null },
      out_of_sample: { sample: "out_of_sample", pnl_pct: 0, expectancy: null, win_rate: null,
                       trade_count: 0, fill_rate: null },
      overfit_gap: 0,
      significance: { n: 0, expectancy: null, std_error: null, win_rate: null,
                      avg_win: null, avg_loss: null, insufficient: true },
      benchmark: { buy_hold_pct: null, all_signals_pct: 0 },
      split_date: null, failed: [], tickers: [],
    });
    vi.stubGlobal("fetch", f);
    await api.optimize({ demo: true, split_ratio: 0.7 });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://localhost:8000/optimize");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ demo: true, split_ratio: 0.7 });
  });

  it("レスポンスが ok でないとき例外を投げる", async () => {
    vi.stubGlobal("fetch", mockFetch("対象銘柄がありません", false, 400));
    await expect(api.getWatchlist()).rejects.toThrow(/API 400/);
  });
});
