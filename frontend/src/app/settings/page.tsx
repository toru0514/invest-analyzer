"use client";

import { useEffect, useState } from "react";
import { api, SignalConfig, WatchItem } from "@/lib/api";
import Disclaimer from "@/components/Disclaimer";

const RULE_LABELS: Record<string, string> = {
  rsi: "RSI（売られすぎ/買われすぎ）",
  ma_cross: "移動平均クロス（GC/DC）",
  macd: "MACD",
  bbands: "ボリンジャーバンド",
  stoch: "ストキャスティクス",
  candle_pattern: "ローソク足パターン（赤三兵/三羽烏/包み足）",
  price_target: "指定金額アラート",
};

export default function Settings() {
  const [watch, setWatch] = useState<WatchItem[]>([]);
  const [configs, setConfigs] = useState<SignalConfig[]>([]);
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    setError(null);
    try {
      const [w, c] = await Promise.all([api.getWatchlist(), api.getConfig()]);
      setWatch(w);
      setConfigs(c);
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function addStock(e: React.FormEvent) {
    e.preventDefault();
    if (!ticker.trim() || !name.trim()) return;
    try {
      await api.addWatch(ticker.trim(), name.trim());
      setTicker("");
      setName("");
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function removeStock(id: number) {
    await api.deleteWatch(id);
    await load();
  }

  function setLocal(id: number, patch: Partial<SignalConfig>) {
    setConfigs((cs) => cs.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }

  async function saveConfigs() {
    setSaved(false);
    try {
      await api.updateConfig(
        configs.map((c) => ({ id: c.id, weight: c.weight, enabled: !!c.enabled })),
      );
      setSaved(true);
      await load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">設定</h1>
      {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <section className="mb-8 rounded border bg-white p-4">
        <h2 className="mb-3 font-semibold">監視銘柄</h2>
        <form onSubmit={addStock} className="mb-4 flex flex-wrap gap-2 text-sm">
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="ティッカー（例: 8306.T）"
            className="rounded border px-2 py-1"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="銘柄名"
            className="rounded border px-2 py-1"
          />
          <button className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700">追加</button>
        </form>
        <ul className="divide-y text-sm">
          {watch.map((w) => (
            <li key={w.id} className="flex items-center justify-between py-2">
              <span>
                <span className="font-mono">{w.ticker}</span> — {w.name}
              </span>
              <button onClick={() => removeStock(w.id)} className="text-red-600 hover:underline">
                削除
              </button>
            </li>
          ))}
          {watch.length === 0 && <li className="py-2 text-slate-500">銘柄がありません。</li>}
        </ul>
      </section>

      <section className="rounded border bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold">シグナル指標（どの技を使うか・重み）</h2>
          <button onClick={saveConfigs} className="rounded bg-green-600 px-3 py-1 text-sm text-white hover:bg-green-700">
            保存
          </button>
        </div>
        {saved && <p className="mb-2 text-sm text-green-700">保存しました。</p>}
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-left">
            <tr>
              <th className="px-3 py-2">指標</th>
              <th className="px-3 py-2">対象</th>
              <th className="px-3 py-2">重み</th>
              <th className="px-3 py-2">有効</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((c) => (
              <tr key={c.id} className="border-t">
                <td className="px-3 py-2">{RULE_LABELS[c.rule_type] ?? c.rule_type}</td>
                <td className="px-3 py-2 text-slate-500">{c.ticker ?? "全銘柄共通"}</td>
                <td className="px-3 py-2">
                  <input
                    type="number"
                    min={0}
                    value={c.weight}
                    onChange={(e) => setLocal(c.id, { weight: Number(e.target.value) })}
                    className="w-16 rounded border px-2 py-0.5"
                  />
                </td>
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    checked={!!c.enabled}
                    onChange={(e) => setLocal(c.id, { enabled: e.target.checked ? 1 : 0 })}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-slate-500">
          スコア閾値（買い ≥ 3 / 売り ≤ -3）と各重みは、シミュレーション結果を見て調整してください。
        </p>
      </section>

      <Disclaimer />
    </div>
  );
}
