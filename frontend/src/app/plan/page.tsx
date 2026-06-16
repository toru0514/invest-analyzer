"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Holding, PlanRow } from "@/lib/api";
import DirectionBadge from "@/components/DirectionBadge";
import Disclaimer from "@/components/Disclaimer";

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
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [prices, setPrices] = useState<Record<string, { date: string; close: number }>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [demo, setDemo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [plan, hs, ps, watch] = await Promise.all([
        api.getPlan(), api.getHoldings(), api.getLatestPrices(), api.getWatchlist(),
      ]);
      setPlanDate(plan.plan_date);
      setRows(plan.rows);
      setHoldings(hs);
      setPrices(ps);
      setNames(Object.fromEntries(watch.map((w) => [w.ticker, w.name])));
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

  const heldMap = new Map(holdings.map((h) => [h.ticker, h]));
  const cards = rows.filter((r) => r.direction !== "neutral" || heldMap.has(r.ticker));
  const watching = rows.filter((r) => r.direction === "neutral" && !heldMap.has(r.ticker));

  // 保有合計（含み損益）
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

      {status && <p className="mb-3 rounded bg-slate-100 px-3 py-2 text-sm text-slate-700">{status}</p>}
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}（Python API :8000 を確認）</p>}

      <p className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        提案指値は「人間が証券アプリで注文を置くための数字」です。発注・自動売買は行いません。
      </p>

      {cards.length === 0 ? (
        <div className="rounded border bg-white p-6 text-center text-sm text-slate-500">
          買い/売りの作戦・保有はありません。「作戦を生成」で再判定するか、銘柄カードで保有を登録してください。
        </div>
      ) : (
        <div className="space-y-3">
          {cards.map((r) => (
            <PlanCard
              key={r.id}
              row={r}
              name={names[r.ticker]}
              holding={heldMap.get(r.ticker) ?? null}
              price={prices[r.ticker]?.close ?? null}
              onChanged={load}
            />
          ))}
        </div>
      )}

      {watching.length > 0 && (
        <section className="mt-6 rounded border bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold text-slate-600">様子見（中立・未保有）</h2>
          <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-500">
            {watching.map((r) => (
              <li key={r.id}>
                <span className="font-mono">{r.ticker}</span>
                {names[r.ticker] && <span className="ml-1">{names[r.ticker]}</span>}
                {r.weekly_trend && <span className="ml-1">（週足 {TREND_LABEL[r.weekly_trend]}）</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <Disclaimer />
    </div>
  );
}

function PlanCard({
  row, name, holding, price, onChanged,
}: { row: PlanRow; name?: string; holding: Holding | null; price: number | null; onChanged: () => Promise<void> }) {
  const actionable = row.direction !== "neutral";
  const pnl = holding && price != null ? (price - holding.avg_cost) * holding.shares : null;
  const pnlPct = holding && price != null ? (price / holding.avg_cost - 1) * 100 : null;

  // 利確/損切に到達したときの保有株での損益（金額換算）
  const targetGain = holding && row.target_price != null ? (row.target_price - holding.avg_cost) * holding.shares : null;
  const stopLoss = holding && row.stop_price != null ? (row.stop_price - holding.avg_cost) * holding.shares : null;

  return (
    <div className="rounded border bg-white p-4">
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <span className="font-mono text-lg font-bold">{row.ticker}</span>
        {name && <span className="font-semibold text-slate-700">{name}</span>}
        <DirectionBadge direction={row.direction} />
        <span className="text-sm text-slate-500">スコア {row.score}</span>
        {row.vol_ratio != null && <span className="text-sm text-slate-500">出来高 {row.vol_ratio.toFixed(2)}倍</span>}
        {row.weekly_trend && (
          <span className={`text-sm ${TREND_CLASS[row.weekly_trend] ?? ""}`}>
            週足 {TREND_LABEL[row.weekly_trend] ?? row.weekly_trend}
          </span>
        )}
        <Link href={`/stocks/${encodeURIComponent(row.ticker)}`} className="ml-auto text-sm text-blue-700 hover:underline">
          チャート →
        </Link>
      </div>

      {/* 保有ポジション（直接入力） */}
      <HoldingEditor ticker={row.ticker} holding={holding} price={price} pnl={pnl} pnlPct={pnlPct} onChanged={onChanged} />

      {actionable ? (
        <>
          <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
            <PlanMetric label={row.direction === "buy" ? "提案指値（買）" : "提案指値（売）"} value={`${yen(row.limit_price)} 円`} accent />
            <PlanMetric label="利確目安" value={`${yen(row.target_price)} 円`} sub={targetGain != null ? `保有なら ${signedYen(targetGain)}` : undefined} cls="text-green-700" />
            <PlanMetric label="損切ライン" value={`${yen(row.stop_price)} 円`} sub={stopLoss != null ? `保有なら ${signedYen(stopLoss)}` : undefined} cls="text-red-700" />
          </div>
          {row.rationale && <p className="mt-2 text-xs text-slate-500">根拠: {row.rationale}</p>}
        </>
      ) : (
        <p className="mt-2 text-xs text-slate-500">判定: 中立（様子見）。保有は上で管理できます。</p>
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
