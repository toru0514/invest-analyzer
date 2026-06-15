"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, PlanRow } from "@/lib/api";
import DirectionBadge from "@/components/DirectionBadge";
import Disclaimer from "@/components/Disclaimer";

const TREND_LABEL: Record<string, string> = { up: "↑ 上昇", down: "↓ 下降", flat: "→ 横ばい" };
const TREND_CLASS: Record<string, string> = {
  up: "text-green-700",
  down: "text-red-700",
  flat: "text-slate-500",
};

function yen(v: number | null) {
  return v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

export default function PlanBoard() {
  const [planDate, setPlanDate] = useState<string | null>(null);
  const [rows, setRows] = useState<PlanRow[]>([]);
  const [demo, setDemo] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const res = await api.getPlan();
      setPlanDate(res.plan_date);
      setRows(res.rows);
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
      setPlanDate(res.plan_date);
      setRows(res.rows);
      setStatus(
        `${res.plan_date} の作戦を生成（${res.rows.length} 銘柄）` +
          (res.failed && res.failed.length ? ` / 取得失敗 ${res.failed.join(", ")}` : ""),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  const actionable = rows.filter((r) => r.direction !== "neutral");

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

      {actionable.length === 0 ? (
        <div className="rounded border bg-white p-6 text-center text-sm text-slate-500">
          買い/売りの作戦はありません（全銘柄が様子見）。「作戦を生成」で再判定できます。
        </div>
      ) : (
        <div className="space-y-3">
          {actionable.map((r) => (
            <div key={r.id} className="rounded border bg-white p-4">
              <div className="mb-2 flex flex-wrap items-center gap-3">
                <span className="font-mono text-lg font-bold">{r.ticker}</span>
                <DirectionBadge direction={r.direction} />
                <span className="text-sm text-slate-500">スコア {r.score}</span>
                {r.vol_ratio != null && (
                  <span className="text-sm text-slate-500">出来高 {r.vol_ratio.toFixed(2)}倍</span>
                )}
                {r.weekly_trend && (
                  <span className={`text-sm ${TREND_CLASS[r.weekly_trend] ?? ""}`}>
                    週足 {TREND_LABEL[r.weekly_trend] ?? r.weekly_trend}
                  </span>
                )}
                <Link href={`/stocks/${encodeURIComponent(r.ticker)}`} className="ml-auto text-sm text-blue-700 hover:underline">
                  チャート →
                </Link>
              </div>
              <div className="grid grid-cols-3 gap-3 text-sm">
                <PlanMetric label={r.direction === "buy" ? "提案指値（買）" : "提案指値（売）"} value={`${yen(r.limit_price)} 円`} accent />
                <PlanMetric label="利確目安" value={`${yen(r.target_price)} 円`} cls="text-green-700" />
                <PlanMetric label="損切ライン" value={`${yen(r.stop_price)} 円`} cls="text-red-700" />
              </div>
              {r.rationale && <p className="mt-2 text-xs text-slate-500">根拠: {r.rationale}</p>}
            </div>
          ))}
        </div>
      )}

      {rows.length > actionable.length && (
        <section className="mt-6 rounded border bg-white p-4">
          <h2 className="mb-2 text-sm font-semibold text-slate-600">様子見（中立）</h2>
          <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-500">
            {rows
              .filter((r) => r.direction === "neutral")
              .map((r) => (
                <li key={r.id}>
                  <span className="font-mono">{r.ticker}</span>
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

function PlanMetric({ label, value, cls, accent }: { label: string; value: string; cls?: string; accent?: boolean }) {
  return (
    <div className={`rounded border p-3 ${accent ? "border-blue-200 bg-blue-50" : ""}`}>
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-lg font-semibold ${cls ?? ""}`}>{value}</div>
    </div>
  );
}
