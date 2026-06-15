import { Direction } from "@/lib/api";

const styles: Record<Direction, string> = {
  buy: "bg-green-100 text-green-800 border-green-300",
  sell: "bg-red-100 text-red-800 border-red-300",
  neutral: "bg-slate-100 text-slate-600 border-slate-300",
};

const labels: Record<Direction, string> = {
  buy: "買い",
  sell: "売り",
  neutral: "中立",
};

export default function DirectionBadge({ direction }: { direction: Direction }) {
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${styles[direction]}`}>
      {labels[direction]}
    </span>
  );
}
