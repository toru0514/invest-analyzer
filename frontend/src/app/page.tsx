"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  SortingState,
} from "@tanstack/react-table";
import { api, RefreshRow, Signal, WatchItem, Direction } from "@/lib/api";
import DirectionBadge from "@/components/DirectionBadge";
import Disclaimer from "@/components/Disclaimer";

type Row = {
  id: number;
  ticker: string;
  name: string;
  price: number | null;
  score: number | null;
  direction: Direction | null;
  date: string | null;
  volRatio: number | null;
  weeklyTrend: string | null;
};

const TREND_LABEL: Record<string, string> = { up: "↑", down: "↓", flat: "→" };
const TREND_CLASS: Record<string, string> = {
  up: "text-green-700",
  down: "text-red-700",
  flat: "text-slate-400",
};

const columnHelper = createColumnHelper<Row>();

export default function Dashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState(true);
  const [status, setStatus] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [watch, signals, latest] = await Promise.all([
        api.getWatchlist(),
        api.getSignals(undefined, 500),
        api.getLatestPrices(),
      ]);
      setRows(mergeRows(watch, signals, latest));
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function refresh() {
    setLoading(true);
    setError(null);
    setStatus(null);
    try {
      const res = await api.refresh(demo);
      const merged = applyRefresh(rows, res.updated);
      setRows(merged.length ? merged : rows);
      await load();
      setStatus(
        `更新 ${res.updated.length} 銘柄` +
          (res.failed.length ? ` / 取得失敗 ${res.failed.join(", ")}（demo を試してください）` : ""),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  const columns = useMemo(
    () => [
      columnHelper.accessor("ticker", {
        header: "銘柄コード",
        cell: (c) => (
          <Link className="font-mono text-blue-700 hover:underline" href={`/stocks/${encodeURIComponent(c.getValue())}`}>
            {c.getValue()}
          </Link>
        ),
      }),
      columnHelper.accessor("name", { header: "銘柄名" }),
      columnHelper.accessor("price", {
        header: "現在値",
        cell: (c) => (c.getValue() == null ? "—" : c.getValue()!.toLocaleString(undefined, { maximumFractionDigits: 1 })),
      }),
      columnHelper.accessor("score", {
        header: "本日スコア",
        cell: (c) => (c.getValue() == null ? "—" : c.getValue()),
      }),
      columnHelper.accessor("volRatio", {
        header: "出来高倍率",
        cell: (c) => {
          const v = c.getValue();
          if (v == null) return "—";
          const cls = v >= 1.5 ? "text-blue-700 font-semibold" : v < 0.7 ? "text-slate-400" : "";
          return <span className={cls}>{v.toFixed(2)}倍</span>;
        },
      }),
      columnHelper.accessor("weeklyTrend", {
        header: "週足",
        cell: (c) => {
          const v = c.getValue();
          if (!v) return "—";
          return <span className={TREND_CLASS[v] ?? ""}>{TREND_LABEL[v] ?? v}</span>;
        },
      }),
      columnHelper.accessor("direction", {
        header: "判定",
        cell: (c) => (c.getValue() ? <DirectionBadge direction={c.getValue()!} /> : "—"),
      }),
      columnHelper.accessor("date", { header: "判定日", cell: (c) => c.getValue() ?? "—" }),
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">ダッシュボード</h1>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
            demo（合成データ）
          </label>
          <button
            onClick={refresh}
            disabled={loading}
            className="rounded bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "更新中…" : "データ更新＋再判定"}
          </button>
        </div>
      </div>

      {status && <p className="mb-3 rounded bg-slate-100 px-3 py-2 text-sm text-slate-700">{status}</p>}
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}（Python API :8000 が起動しているか確認）</p>}

      <div className="overflow-x-auto rounded border bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    onClick={h.column.getToggleSortingHandler()}
                    className="cursor-pointer select-none px-3 py-2 font-semibold"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ▲", desc: " ▼" }[h.column.getIsSorted() as string] ?? ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((r) => (
              <tr key={r.id} className="border-t hover:bg-slate-50">
                {r.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-500">
                  監視銘柄がありません。設定画面で追加してください。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Disclaimer />
    </div>
  );
}

function mergeRows(
  watch: WatchItem[],
  signals: Signal[],
  prices: Record<string, { date: string; close: number }> = {},
): Row[] {
  const latest = new Map<string, Signal>();
  for (const s of signals) {
    if (!latest.has(s.ticker)) latest.set(s.ticker, s); // signals は新しい順
  }
  return watch.map((w) => {
    const s = latest.get(w.ticker);
    const p = prices[w.ticker];
    const vr = s && typeof s.detail?.vol_ratio === "number" ? (s.detail.vol_ratio as number) : null;
    const wt = s && typeof s.detail?.weekly_trend === "string" ? (s.detail.weekly_trend as string) : null;
    return {
      id: w.id,
      ticker: w.ticker,
      name: w.name,
      price: p ? p.close : null,
      score: s ? s.score : null,
      direction: s ? s.direction : null,
      date: s ? s.date : null,
      volRatio: vr,
      weeklyTrend: wt,
    };
  });
}

function applyRefresh(rows: Row[], updated: RefreshRow[]): Row[] {
  const byTicker = new Map(updated.map((u) => [u.ticker, u]));
  return rows.map((r) => {
    const u = byTicker.get(r.ticker);
    if (!u) return r;
    const vr = typeof u.detail?.vol_ratio === "number" ? (u.detail.vol_ratio as number) : r.volRatio;
    const wt = typeof u.detail?.weekly_trend === "string" ? (u.detail.weekly_trend as string) : r.weeklyTrend;
    return { ...r, price: u.price, score: u.score, direction: u.direction, date: u.date, volRatio: vr, weeklyTrend: wt };
  });
}
