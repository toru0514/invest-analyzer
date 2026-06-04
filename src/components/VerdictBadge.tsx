import type { Verdict } from "@/lib/types";
import { VERDICT_META } from "@/lib/analyze";

const STYLES: Record<Verdict, string> = {
  buy: "bg-emerald-100 text-emerald-700 ring-emerald-200",
  hold: "bg-amber-100 text-amber-700 ring-amber-200",
  avoid: "bg-rose-100 text-rose-700 ring-rose-200",
};

export function VerdictBadge({
  verdict,
  size = "md",
}: {
  verdict: Verdict;
  size?: "sm" | "md";
}) {
  const meta = VERDICT_META[verdict];
  const sizing = size === "sm" ? "px-2.5 py-0.5 text-xs" : "px-3 py-1 text-sm";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold ring-1 ${STYLES[verdict]} ${sizing}`}
    >
      {meta.label}
    </span>
  );
}
