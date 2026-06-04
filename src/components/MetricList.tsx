import type { MetricResult, Rating } from "@/lib/types";

const DOT: Record<Rating, string> = {
  good: "bg-emerald-500",
  neutral: "bg-amber-500",
  bad: "bg-rose-500",
};

const BAR: Record<Rating, string> = {
  good: "bg-emerald-500",
  neutral: "bg-amber-500",
  bad: "bg-rose-500",
};

export function MetricList({ metrics }: { metrics: MetricResult[] }) {
  return (
    <ul className="divide-y divide-slate-100">
      {metrics.map((m) => (
        <li key={m.key} className="py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${DOT[m.rating]}`} />
              <span className="text-sm font-medium text-slate-700">
                {m.label}
              </span>
            </div>
            <span className="text-sm font-semibold tabular-nums text-slate-900">
              {m.display}
            </span>
          </div>
          <div className="mt-2 flex items-center gap-3 pl-4">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div
                className={`h-full rounded-full ${BAR[m.rating]}`}
                style={{ width: `${m.score}%` }}
              />
            </div>
            <span className="w-8 text-right text-xs tabular-nums text-slate-400">
              {m.score}
            </span>
          </div>
          <p className="mt-1 pl-4 text-xs text-slate-500">{m.comment}</p>
        </li>
      ))}
    </ul>
  );
}
