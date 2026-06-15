"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { api, Candle, Signal } from "@/lib/api";
import CandleChart from "@/components/CandleChart";
import DirectionBadge from "@/components/DirectionBadge";
import Disclaimer from "@/components/Disclaimer";

export default function StockDetail({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker: raw } = use(params);
  const ticker = decodeURIComponent(raw);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [c, s] = await Promise.all([api.getPrices(ticker), api.getSignals(ticker, 50)]);
        setCandles(c);
        setSignals(s);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [ticker]);

  const last = candles.at(-1);

  return (
    <div>
      <Link href="/" className="text-sm text-blue-700 hover:underline">
        ← ダッシュボード
      </Link>
      <h1 className="mt-2 mb-4 text-xl font-bold">
        <span className="font-mono">{ticker}</span> 銘柄詳細
      </h1>

      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {last && (
        <div className="mb-4 flex gap-6 text-sm">
          <div>
            <div className="text-slate-500">直近終値</div>
            <div className="text-lg font-semibold">{last.close.toLocaleString(undefined, { maximumFractionDigits: 1 })}</div>
          </div>
          <div>
            <div className="text-slate-500">出来高</div>
            <div className="text-lg font-semibold">{last.volume.toLocaleString()}</div>
          </div>
          <div>
            <div className="text-slate-500">日付</div>
            <div className="text-lg font-semibold">{last.date}</div>
          </div>
        </div>
      )}

      <section className="mb-6 rounded border bg-white p-4">
        <h2 className="mb-3 font-semibold">ローソク足チャート</h2>
        <CandleChart candles={candles} />
      </section>

      <section className="rounded border bg-white p-4">
        <h2 className="mb-3 font-semibold">シグナル履歴</h2>
        {signals.length === 0 ? (
          <p className="text-sm text-slate-500">履歴がありません。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-100 text-left">
                <tr>
                  <th className="px-3 py-2">日付</th>
                  <th className="px-3 py-2">スコア</th>
                  <th className="px-3 py-2">判定</th>
                  <th className="px-3 py-2">内訳</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id} className="border-t">
                    <td className="px-3 py-2">{s.date}</td>
                    <td className="px-3 py-2">{s.score}</td>
                    <td className="px-3 py-2">
                      <DirectionBadge direction={s.direction} />
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600">
                      {Object.entries(s.detail).map(([k, v]) => `${k}:${v}`).join(", ") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Disclaimer />
    </div>
  );
}
