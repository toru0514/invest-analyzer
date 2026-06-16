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
    const f = mockFetch({ sweep: [], best: null, baseline_pnl_pct: 0, contributions: [], failed: [], tickers: [], days: 40 });
    vi.stubGlobal("fetch", f);
    await api.optimize({ days: 30, demo: true });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("http://localhost:8000/optimize");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ days: 30, demo: true });
  });

  it("レスポンスが ok でないとき例外を投げる", async () => {
    vi.stubGlobal("fetch", mockFetch("対象銘柄がありません", false, 400));
    await expect(api.getWatchlist()).rejects.toThrow(/API 400/);
  });
});
