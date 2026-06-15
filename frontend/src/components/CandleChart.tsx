"use client";

import { useEffect, useRef } from "react";
import { createChart, ColorType, IChartApi, CandlestickData, Time } from "lightweight-charts";
import { Candle } from "@/lib/api";

export default function CandleChart({ candles }: { candles: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || candles.length === 0) return;

    const chart: IChartApi = createChart(ref.current, {
      height: 360,
      layout: { background: { type: ColorType.Solid, color: "#ffffff" }, textColor: "#334155" },
      grid: { vertLines: { color: "#f1f5f9" }, horzLines: { color: "#f1f5f9" } },
      timeScale: { borderColor: "#cbd5e1" },
      rightPriceScale: { borderColor: "#cbd5e1" },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#16a34a",
      downColor: "#dc2626",
      borderUpColor: "#16a34a",
      borderDownColor: "#dc2626",
      wickUpColor: "#16a34a",
      wickDownColor: "#dc2626",
    });

    const data: CandlestickData[] = candles.map((c) => ({
      time: c.date as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    series.setData(data);
    chart.timeScale().fitContent();

    const onResize = () => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 });
    onResize();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [candles]);

  if (candles.length === 0) {
    return <p className="text-sm text-slate-500">価格データがありません。先にダッシュボードで「データ更新」を実行してください。</p>;
  }
  return <div ref={ref} className="w-full" />;
}
