export function ScoreGauge({ score }: { score: number }) {
  const color =
    score >= 65
      ? "text-emerald-600"
      : score >= 45
      ? "text-amber-600"
      : "text-rose-600";
  const track =
    score >= 65
      ? "stroke-emerald-500"
      : score >= 45
      ? "stroke-amber-500"
      : "stroke-rose-500";

  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - score / 100);

  return (
    <div className="relative grid h-28 w-28 place-items-center">
      <svg className="h-28 w-28 -rotate-90" viewBox="0 0 100 100">
        <circle
          cx="50"
          cy="50"
          r={radius}
          className="fill-none stroke-slate-200"
          strokeWidth="8"
        />
        <circle
          cx="50"
          cy="50"
          r={radius}
          className={`fill-none ${track}`}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className={`text-2xl font-bold ${color}`}>{score}</span>
        <span className="text-[10px] text-slate-400">/ 100</span>
      </div>
    </div>
  );
}
