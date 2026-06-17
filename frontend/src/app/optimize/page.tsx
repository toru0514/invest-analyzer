"use client";

import { useState } from "react";
import { api, OptimizeResponse } from "@/lib/api";
import Disclaimer from "@/components/Disclaimer";

const RULE_LABELS: Record<string, string> = {
  rsi: "RSI", ma_cross: "移動平均クロス", macd: "MACD", bbands: "ボリンジャーバンド",
  stoch: "ストキャスティクス", candle_pattern: "ローソク足パターン", disparity: "乖離率",
  obv: "OBV", cci: "CCI", volume_filter: "出来高フィルター", weekly_trend_filter: "週足トレンド足切り",
};

function pct(v: number | null | undefined) {
  if (v == null) return "N/A";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export default function Optimize() {
  const [splitRatio, setSplitRatio] = useState(0.7);
  const [demo, setDemo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [res, setRes] = useState<OptimizeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);

  async function run() {
    setLoading(true); setError(null); setApplied(null);
    try {
      setRes(await api.optimize({ demo, split_ratio: splitRatio }));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function applyBest() {
    if (!res) return;
    const th = res.chosen_params.threshold;
    try {
      await api.updateSettings({ buy_threshold: th, sell_threshold: -th });
      setApplied(`スコア閾値を ±${th} に適用しました（設定に保存）。`);
    } catch (e) {
      setError(String(e));
    }
  }

  const maxAbsDelta = res
    ? Math.max(1, ...res.in_sample.contributions.map((c) => Math.abs(c.delta)))
    : 1;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">最適化（チューニング）</h1>
          <p className="text-sm text-slate-500">
            前半(in-sample)で閾値を選び、後半(out-of-sample)で検証します。見出しは後半＝未使用期間の成績です。
          </p>
        </div>
        <div className="flex items-end gap-3 text-sm">
          <label className="flex flex-col gap-1">
            前半比率（in-sample）
            <input type="number" step="0.05" min="0.5" max="0.9" value={splitRatio}
              onChange={(e) => setSplitRatio(Number(e.target.value))}
              className="w-24 rounded border px-2 py-1" />
          </label>
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} />
            demo（合成データ）
          </label>
          <button onClick={run} disabled={loading}
            className="rounded bg-blue-600 px-3 py-2 text-white hover:bg-blue-700 disabled:opacity-50">
            {loading ? "計算中…（数十秒）" : "最適化を実行"}
          </button>
        </div>
      </div>

      {applied && <p className="mb-3 rounded bg-green-50 px-3 py-2 text-sm text-green-700">{applied}</p>}
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}（Python API :8000 を確認）</p>}

      {res && (
        <>
          {res.failed.length > 0 && (
            <p className="mb-3 rounded bg-amber-50 px-3 py-2 text-sm text-amber-800">
              取得失敗: {res.failed.join(", ")}（demo を有効にして再実行してください）
            </p>
          )}

          <section className="mb-6 rounded border border-blue-200 bg-blue-50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm">
                <div className="text-slate-500">
                  推奨設定（対象 {res.tickers.join(", ")}{res.split_date ? ` / 分割 ${res.split_date}` : ""}）
                </div>
                <div className="text-lg font-semibold">
                  スコア閾値 ±{res.chosen_params.threshold}
                  <span className="ml-2 text-green-700">OOS {pct(res.out_of_sample.pnl_pct)}</span>
                </div>
                <div className="mt-1 text-slate-600">
                  OOS勝率 {res.out_of_sample.win_rate == null ? "N/A" : `${res.out_of_sample.win_rate.toFixed(1)}%`}
                  ・取引 {res.out_of_sample.trade_count}回
                  ・約定率 {res.out_of_sample.fill_rate == null ? "N/A" : `${(res.out_of_sample.fill_rate * 100).toFixed(0)}%`}
                  ・過学習ギャップ {res.overfit_gap.toFixed(1)}
                </div>
                {res.significance.insufficient && (
                  <div className="mt-1 text-amber-700">
                    ⚠ トレード数 {res.significance.n} 件は統計的に不十分（誤差範囲）。期間・銘柄を増やして再検証を。
                  </div>
                )}
                <div className="mt-1 text-slate-600">
                  ベンチマーク: buy&hold {pct(res.benchmark.buy_hold_pct)} / 全シグナル等加重 {pct(res.benchmark.all_signals_pct)}
                </div>
              </div>
              <button onClick={applyBest} className="rounded bg-green-600 px-3 py-2 text-sm text-white hover:bg-green-700">
                この閾値を適用
              </button>
            </div>
          </section>

          <section className="mb-6 rounded border bg-white p-4">
            <h2 className="mb-3 font-semibold">閾値スイープ（前半 in-sample・参考）</h2>
            <table className="w-full text-sm">
              <thead className="bg-slate-100 text-left">
                <tr>
                  <th className="px-3 py-2">スコア閾値</th>
                  <th className="px-3 py-2">損益</th>
                  <th className="px-3 py-2">期待値/件</th>
                  <th className="px-3 py-2">勝率</th>
                  <th className="px-3 py-2">取引回数</th>
                </tr>
              </thead>
              <tbody>
                {res.in_sample.sweep.map((s, i) => (
                  <tr key={i} className={`border-t ${s.threshold === res.chosen_params.threshold ? "bg-green-50" : ""}`}>
                    <td className="px-3 py-2">±{s.threshold}</td>
                    <td className={`px-3 py-2 font-semibold ${s.pnl_pct >= 0 ? "text-green-700" : "text-red-700"}`}>{pct(s.pnl_pct)}</td>
                    <td className="px-3 py-2">{s.expectancy == null ? "N/A" : s.expectancy.toFixed(1)}</td>
                    <td className="px-3 py-2">{s.win_rate == null ? "N/A" : `${s.win_rate.toFixed(1)}%`}</td>
                    <td className="px-3 py-2">{s.trade_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="rounded border bg-white p-4">
            <h2 className="mb-1 font-semibold">指標の寄与度（leave-one-out・前半 in-sample）</h2>
            <p className="mb-3 text-xs text-slate-500">
              その指標を外したときの損益悪化幅（＝寄与度）。プラスが大きいほど有効、マイナスは足を引っ張っている可能性。
              基準（全指標）損益 {pct(res.in_sample.baseline_pnl_pct)}。
            </p>
            <ul className="space-y-1.5">
              {res.in_sample.contributions.map((c) => (
                <li key={c.rule_type} className="flex items-center gap-2 text-sm">
                  <span className="w-40 shrink-0">{RULE_LABELS[c.rule_type] ?? c.rule_type}</span>
                  <span className="flex-1">
                    <span className={`inline-block h-3 rounded ${c.delta >= 0 ? "bg-green-500" : "bg-red-400"}`}
                      style={{ width: `${(Math.abs(c.delta) / maxAbsDelta) * 100}%`, minWidth: c.delta === 0 ? 0 : 4 }} />
                  </span>
                  <span className={`w-20 text-right font-mono ${c.delta >= 0 ? "text-green-700" : "text-red-700"}`}>
                    {c.delta >= 0 ? "+" : ""}{c.delta.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      <Disclaimer />
    </div>
  );
}
