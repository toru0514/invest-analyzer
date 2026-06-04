import type { FiscalYear } from "@/lib/types";

const yen = (v: number) => v.toLocaleString("ja-JP");

interface Row {
  label: string;
  get: (fy: FiscalYear) => number;
  /** マイナスを赤字表示するか */
  highlightNegative?: boolean;
}

const ROWS: Row[] = [
  { label: "売上高", get: (f) => f.revenue },
  { label: "営業利益", get: (f) => f.operatingIncome, highlightNegative: true },
  { label: "当期純利益", get: (f) => f.netIncome, highlightNegative: true },
  { label: "総資産", get: (f) => f.totalAssets },
  { label: "自己資本", get: (f) => f.equity },
  { label: "有利子負債", get: (f) => f.interestBearingDebt },
  { label: "営業CF", get: (f) => f.operatingCashFlow, highlightNegative: true },
  { label: "投資CF", get: (f) => f.investingCashFlow, highlightNegative: true },
];

export function FinancialTable({ years }: { years: FiscalYear[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-slate-500">
            <th className="py-2 pr-4 font-medium">項目（百万円）</th>
            {years.map((y) => (
              <th key={y.year} className="py-2 px-3 text-right font-medium">
                {y.year}年度
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr
              key={row.label}
              className="border-b border-slate-50 last:border-0"
            >
              <td className="py-2 pr-4 text-slate-600">{row.label}</td>
              {years.map((y) => {
                const v = row.get(y);
                const neg = row.highlightNegative && v < 0;
                return (
                  <td
                    key={y.year}
                    className={`py-2 px-3 text-right tabular-nums ${
                      neg ? "text-rose-600" : "text-slate-900"
                    }`}
                  >
                    {yen(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
