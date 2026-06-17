"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Holding, PlanRow, WatchItem } from "@/lib/api";
import DirectionBadge from "@/components/DirectionBadge";
import Disclaimer from "@/components/Disclaimer";
import StockAddSearch from "@/components/StockAddSearch";

const TREND_LABEL: Record<string, string> = { up: "↑ 上昇", down: "↓ 下降", flat: "→ 横ばい" };
const TREND_CLASS: Record<string, string> = {
  up: "text-green-700",
  down: "text-red-700",
  flat: "text-slate-500",
};

function yen(v: number | null | undefined) {
  return v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
function signedYen(v: number) {
  return `${v >= 0 ? "+" : ""}${Math.round(v).toLocaleString()} 円`;
}

export default function PlanBoard() {
  const [planDate, setPlanDate] = useState<string | null>(null);
  const [rows, setRows] = useState<PlanRow[]>([]);
  const [watch, setWatch] = useState<WatchItem[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [prices, setPrices] = useState<Record<string, { date: string; close: number }>>({});
  const [demo, setDemo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [plan, hs, ps, w] = await Promise.all([
        api.getPlan(), api.getHoldings(), api.getLatestPrices(), api.getWatchlist(),
      ]);
      setPlanDate(plan.plan_date);
      setRows(plan.rows);
      setHoldings(hs);
      setPrices(ps);
      setWatch(w);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function generate() {
    setLoading(true);
    setError(null);
    setStatus(null);
    try {
      const res = await api.generatePlan(demo);
      setStatus(
        `${res.plan_date} の作戦を生成（${res.rows.length} 銘柄）` +
          (res.failed && res.failed.length ? ` / 取得失敗 ${res.failed.join(", ")}` : ""),
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  const planByTicker = new Map(rows.map((r) => [r.ticker, r]));
  const heldMap = new Map(holdings.map((h) => [h.ticker, h]));

  // 並び順: 買い/売り → 保有 → 中立(作戦あり) → 作戦未生成
  function prio(ticker: string): number {
    const r = planByTicker.get(ticker);
    if (r && r.direction !== "neutral") return 0;
    if (heldMap.has(ticker)) return 1;
    if (r) return 2;
    return 3;
  }
  const ordered = [...watch].sort((a, b) => prio(a.ticker) - prio(b.ticker));

  const totalPnl = holdings.reduce((acc, h) => {
    const p = prices[h.ticker]?.close;
    return p != null ? acc + (p - h.avg_cost) * h.shares : acc;
  }, 0);
  const hasHeldPrices = holdings.some((h) => prices[h.ticker]?.close != null);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">作戦ボード</h1>
          <p className="text-sm text-slate-500">
            {planDate ? `${planDate} 寄付き前の作戦` : "まだ作戦がありません"}
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {holdings.length > 0 && hasHeldPrices && (
            <span className="text-sm">
              保有合計の含み損益:{" "}
              <span className={`font-semibold ${totalPnl >= 0 ? "text-green-700" : "text-red-700"}`}>
                {signedYen(totalPnl)}
              </span>
            </span>
          )}
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
            demo（合成データ）
          </label>
          <button
            onClick={generate}
            disabled={loading}
            className="rounded bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "生成中…" : "作戦を生成（再判定）"}
          </button>
        </div>
      </div>

      {/* 銘柄追加（検索して選択 / コード入力） */}
      <div className="mb-3">
        <StockAddSearch onAdded={load} />
      </div>

      {status && <p className="mb-3 rounded bg-slate-100 px-3 py-2 text-sm text-slate-700">{status}</p>}
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        提案指値は「人間が証券アプリで注文を置くための数字」です。発注・自動売買は行いません。
      </p>

      {ordered.length === 0 ? (
        <div className="rounded border bg-white p-6 text-center text-sm text-slate-500">
          監視銘柄がありません。上の「銘柄を追加」から登録してください。
        </div>
      ) : (
        <div className="space-y-3">
          {ordered.map((w) => (
            <PlanCard
              key={w.id}
              ticker={w.ticker}
              name={w.name}
              row={planByTicker.get(w.ticker) ?? null}
              holding={heldMap.get(w.ticker) ?? null}
              price={prices[w.ticker]?.close ?? null}
              onChanged={load}
            />
          ))}
        </div>
      )}

      <Disclaimer />
    </div>
  );
}

function PlanCard({
  ticker, name, row, holding, price, onChanged,
}: {
  ticker: string; name?: string; row: PlanRow | null; holding: Holding | null;
  price: number | null; onChanged: () => Promise<void>;
}) {
  const actionable = row != null && row.direction !== "neutral";
  const pnl = holding && price != null ? (price - holding.avg_cost) * holding.shares : null;
  const pnlPct = holding && price != null ? (price / holding.avg_cost - 1) * 100 : null;
  const targetGain = holding && row?.target_price != null ? (row.target_price - holding.avg_cost) * holding.shares : null;
  const stopLoss = holding && row?.stop_price != null ? (row.stop_price - holding.avg_cost) * holding.shares : null;

  return (
    <div className="rounded border bg-white p-4">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <span className="font-mono text-lg font-bold">{ticker}</span>
        {name && <span className="font-semibold text-slate-700">{name}</span>}
        {row ? <DirectionBadge direction={row.direction} /> : <span className="text-xs text-slate-400">作戦未生成</span>}
        {row && <span className="text-sm text-slate-500">スコア {row.score}</span>}
        {row?.vol_ratio != null && <span className="text-sm text-slate-500">出来高 {row.vol_ratio.toFixed(2)}倍</span>}
        {row?.weekly_trend && (
          <span className={`text-sm ${TREND_CLASS[row.weekly_trend] ?? ""}`}>
            週足 {TREND_LABEL[row.weekly_trend] ?? row.weekly_trend}
          </span>
        )}
        <Link href={`/stocks/${encodeURIComponent(ticker)}`} className="ml-auto text-sm text-blue-700 hover:underline">
          チャート →
        </Link>
      </div>

      <HoldingEditor ticker={ticker} holding={holding} price={price} pnl={pnl} pnlPct={pnlPct} onChanged={onChanged} />

      {actionable ? (
        <>
          <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
            <PlanMetric label={row!.direction === "buy" ? "提案指値（買）" : "提案指値（売）"} value={`${yen(row!.limit_price)} 円`} accent />
            <PlanMetric label="利確目安" value={`${yen(row!.target_price)} 円`} sub={targetGain != null ? `保有なら ${signedYen(targetGain)}` : undefined} cls="text-green-700" />
            <PlanMetric label="損切ライン" value={`${yen(row!.stop_price)} 円`} sub={stopLoss != null ? `保有なら ${signedYen(stopLoss)}` : undefined} cls="text-red-700" />
          </div>
          {row!.rationale && <p className="mt-2 text-xs text-slate-500">根拠: {row!.rationale}</p>}
        </>
      ) : holding && row && row.target_price != null ? (
        <>
          <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <PlanMetric label="利確目安（保有者向け）" value={`${yen(row.target_price)} 円`} sub={targetGain != null ? `保有なら ${signedYen(targetGain)}` : undefined} cls="text-green-700" />
            <PlanMetric label="損切ライン（保有者向け）" value={`${yen(row.stop_price)} 円`} sub={stopLoss != null ? `保有なら ${signedYen(stopLoss)}` : undefined} cls="text-red-700" />
          </div>
          <p className="mt-2 text-xs text-slate-500">判定は中立（様子見）。上は保有ポジション向けの ATR ベースの出口の目安です。</p>
        </>
      ) : (
        <p className="mt-2 text-xs text-slate-500">
          {row ? "判定: 中立（様子見）。保有は上で管理できます。" : "まだ作戦がありません。「作戦を生成」で判定・価格を取得してください。"}
        </p>
      )}
    </div>
  );
}

function HoldingEditor({
  ticker, holding, price, pnl, pnlPct, onChanged,
}: {
  ticker: string; holding: Holding | null; price: number | null;
  pnl: number | null; pnlPct: number | null; onChanged: () => Promise<void>;
}) {
  const [cost, setCost] = useState("");
  const [shares, setShares] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setCost(holding ? String(holding.avg_cost) : "");
    setShares(holding ? String(holding.shares) : "");
  }, [holding]);

  async function save() {
    setBusy(true);
    try {
      await api.saveHolding({ ticker, avg_cost: Number(cost) || 0, shares: Number(shares) || 0 });
      await onChanged();
    } finally {
      setBusy(false);
    }
  }
  async function clear() {
    setBusy(true);
    try {
      await api.deleteHolding(ticker);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded bg-slate-50 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-xs font-semibold text-slate-500">保有</span>
        <label className="flex items-center gap-1 text-xs text-slate-600">
          取得単価
          <input type="number" value={cost} onChange={(e) => setCost(e.target.value)} placeholder="例: 3210"
            className="w-24 rounded border px-2 py-0.5" />
        </label>
        <label className="flex items-center gap-1 text-xs text-slate-600">
          株数
          <input type="number" value={shares} onChange={(e) => setShares(e.target.value)} placeholder="例: 100"
            className="w-24 rounded border px-2 py-0.5" />
        </label>
        <button onClick={save} disabled={busy} className="rounded bg-green-600 px-2 py-0.5 text-xs text-white hover:bg-green-700 disabled:opacity-50">
          保存
        </button>
        {holding && (
          <button onClick={clear} disabled={busy} className="text-xs text-red-600 hover:underline">保有を解除</button>
        )}
        {holding && price != null && (
          <span className="ml-auto flex items-center gap-3">
            <span className="text-xs text-slate-500">現在値 {yen(price)} 円</span>
            <span className="text-xs text-slate-500">評価額 {Math.round(price * holding.shares).toLocaleString()} 円</span>
            <span className={`font-semibold ${pnl != null && pnl >= 0 ? "text-green-700" : "text-red-700"}`}>
              含み損益 {pnl != null ? signedYen(pnl) : "—"}
              {pnlPct != null && ` (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%)`}
            </span>
          </span>
        )}
        {holding && price == null && (
          <span className="ml-auto text-xs text-slate-400">現在値は「作戦を生成」で取得</span>
        )}
      </div>
    </div>
  );
}

function PlanMetric({ label, value, cls, accent, sub }: { label: string; value: string; cls?: string; accent?: boolean; sub?: string }) {
  return (
    <div className={`rounded border p-3 ${accent ? "border-blue-200 bg-blue-50" : ""}`}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-lg font-semibold ${cls ?? ""}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}
