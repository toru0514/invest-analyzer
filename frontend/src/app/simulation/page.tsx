"use client";

import { useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { api, BacktestResult } from "@/lib/api";
import { buildBacktestBody } from "@/lib/backtest";
import DirectionBadge from "@/components/DirectionBadge";
import Disclaimer from "@/components/Disclaimer";

export default function Simulation() {
  const [capital, setCapital] = useState(3000);
  const [days, setDays] = useState(22);
  const [demo, setDemo] = useState(true);
  const [persist, setPersist] = useState(false);
  const [atrExit, setAtrExit] = useState(false);
  const [trailAtrMult, setTrailAtrMult] = useState(0);
  const [maxHoldDays, setMaxHoldDays] = useState(0);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const r = await api.backtest(
        buildBacktestBody({ capital, days, demo, persist, atrExit, trailAtrMult, maxHoldDays }),
      );
      setResult(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">シミュレーション（ペーパートレード）</h1>

      <section className="mb-6 rounded border bg-white p-4">
        <div className="flex flex-wrap items-end gap-4 text-sm">
          <label className="flex flex-col gap-1">
            仮想資金（円）
            <input
              type="number"
              value={capital}
              onChange={(e) => setCapital(Number(e.target.value))}
              className="w-32 rounded border px-2 py-1"
            />
          </label>
          <label className="flex flex-col gap-1">
            評価営業日数
            <input
              type="number"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="w-32 rounded border px-2 py-1"
            />
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
            demo（合成データ）
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={persist} onChange={(e) => setPersist(e.target.checked)} />
            取引を記録（paper_trades）
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={atrExit} onChange={(e) => setAtrExit(e.target.checked)} />
            ATR出口ルールを使う（押し目指値＋損切/利確）
          </label>
          {atrExit && (
            <>
              <label className="flex flex-col gap-1">
                トレーリングATR倍率（0=OFF）
                <input
                  type="number"
                  step="0.5"
                  value={trailAtrMult}
                  onChange={(e) => setTrailAtrMult(Number(e.target.value))}
                  className="w-40 rounded border px-2 py-1"
                />
              </label>
              <label className="flex flex-col gap-1">
                最大保有日数（0=OFF）
                <input
                  type="number"
                  value={maxHoldDays}
                  onChange={(e) => setMaxHoldDays(Number(e.target.value))}
                  className="w-40 rounded border px-2 py-1"
                />
              </label>
            </>
          )}
          <button
            onClick={run}
            disabled={loading}
            className="rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "実行中…" : "バックテスト実行"}
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          watchlist の有効銘柄を対象に、過去データで端株（小数株）を許容して売買を再現します。未来データは使いません。
          {atrExit
            ? "（ATR出口: ハーフATRの押し目で約定し、損切/利確ラインまたは逆シグナルで決済）"
            : "（既定: スコアが反転したら決済）"}
        </p>
      </section>

      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {result && (
        <>
          {result.failed && result.failed.length > 0 && (
            <p className="mb-3 rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
              取得失敗: {result.failed.join(", ")}（demo を有効にして再実行してください）
            </p>
          )}

          <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3">
            <Metric label="開始資金" value={`${result.initial.toLocaleString()} 円`} />
            <Metric label="最終評価額" value={`${result.final.toLocaleString(undefined, { maximumFractionDigits: 1 })} 円`} />
            <Metric
              label="損益"
              value={`${result.pnl_amount >= 0 ? "+" : ""}${result.pnl_amount.toLocaleString(undefined, { maximumFractionDigits: 1 })} 円 (${result.pnl_pct.toFixed(2)}%)`}
              positive={result.pnl_amount >= 0}
            />
            <Metric label="取引回数" value={`${result.trade_count} 回（決済 ${result.closed_trades}）`} />
            <Metric label="勝率" value={result.win_rate == null ? "N/A" : `${result.win_rate.toFixed(1)}%`} />
            <Metric label="最大ドローダウン" value={`${result.max_drawdown_pct.toFixed(2)}%`} />
          </section>

          {result.exit_mode === "plan" && (
            <section className="mb-6">
              <h2 className="mb-2 text-sm font-semibold text-slate-600">ATR出口の内訳（強化J）</h2>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <Metric label="利確で決済" value={`${result.take_profit_count ?? 0} 回`} />
                <Metric label="損切で決済" value={`${result.stop_loss_count ?? 0} 回`} />
                <Metric label="逆シグナルで決済" value={`${result.signal_exit_count ?? 0} 回`} />
                <Metric label="トレーリングで決済" value={`${result.trail_exit_count ?? 0} 回`} />
                <Metric label="時間切れで決済" value={`${result.time_exit_count ?? 0} 回`} />
                <Metric label="平均保有日数" value={result.avg_holding_days == null ? "N/A" : `${result.avg_holding_days.toFixed(1)} 日`} />
                <Metric label="リスクリワード実績" value={result.risk_reward == null ? "N/A" : `${result.risk_reward.toFixed(2)} : 1`} />
              </div>
            </section>
          )}

          <section className="mb-6 rounded border bg-white p-4">
            <h2 className="mb-3 font-semibold">資産推移</h2>
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={result.equity_curve}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                <YAxis domain={["auto", "auto"]} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="equity" stroke="#2563eb" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </section>

          <section className="rounded border bg-white p-4">
            <h2 className="mb-3 font-semibold">約定ログ</h2>
            {result.trades.length === 0 ? (
              <p className="text-sm text-slate-500">期間中の取引はありませんでした。</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-slate-100 text-left">
                    <tr>
                      <th className="px-3 py-2">日付</th>
                      <th className="px-3 py-2">銘柄</th>
                      <th className="px-3 py-2">売買</th>
                      <th className="px-3 py-2">価格</th>
                      <th className="px-3 py-2">株数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t, i) => (
                      <tr key={i} className="border-t">
                        <td className="px-3 py-2">{t.date}</td>
                        <td className="px-3 py-2 font-mono">{t.ticker}</td>
                        <td className="px-3 py-2">
                          <DirectionBadge direction={t.action === "buy" ? "buy" : "sell"} />
                        </td>
                        <td className="px-3 py-2">{t.price.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
                        <td className="px-3 py-2">{t.shares.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      <Disclaimer />
    </div>
  );
}

function Metric({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="rounded border bg-white p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-lg font-semibold ${positive === undefined ? "" : positive ? "text-green-700" : "text-red-700"}`}>
        {value}
      </div>
    </div>
  );
}
