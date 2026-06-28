import type { PerfRow } from "@/lib/api";

const pctFrac = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);   // 0..1
const pct100 = (x: number | null) => (x == null ? "—" : `${Math.round(x)}%`);            // 0..100
const num = (x: number | null, d = 2) => (x == null ? "—" : x.toFixed(d));

/** 型別成績テーブル（純表示・打ち手11）。rows が空なら案内文。 */
export default function PerfTable({ rows }: { rows: PerfRow[] }) {
  if (!rows.length) {
    return (
      <p className="text-sm text-slate-500">
        実績が貯まると、ここに型別の成績（約定率・勝率・平均R）が表示されます。
      </p>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-slate-500">
          <th className="py-1">型（地合い:方向）</th>
          <th>作戦数</th>
          <th>約定率</th>
          <th>勝率</th>
          <th>平均R</th>
          <th>平均日数</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.type} className="border-t">
            <td className="py-1 font-medium">{r.type}</td>
            <td>{r.n_plans}</td>
            <td>{pctFrac(r.fill_rate)}</td>
            <td>
              {pct100(r.win_rate)}
              {r.n_resolved ? ` (${r.n_resolved})` : ""}
            </td>
            <td className={r.avg_r != null && r.avg_r > 0 ? "text-green-700" : "text-red-700"}>
              {num(r.avg_r)}
            </td>
            <td>{num(r.avg_days, 1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
