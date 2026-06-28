import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import PerfTable from "@/components/PerfTable";

const ROWS = [
  { type: "risk_on:buy", n_plans: 10, n_filled: 7, fill_rate: 0.7,
    n_resolved: 5, win_rate: 40, avg_r: 1.2, avg_days: 4 },
];

describe("PerfTable", () => {
  it("型別の成績行を描画する（約定率は 0..1 を % 表示）", () => {
    render(<PerfTable rows={ROWS} />);
    expect(screen.getByText("risk_on:buy")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();   // fill_rate 0.7 → 70%
    expect(screen.getByText("1.20")).toBeInTheDocument();  // avg_r
  });

  it("空のときは案内メッセージを出す", () => {
    render(<PerfTable rows={[]} />);
    expect(screen.getByText(/実績が貯まると/)).toBeInTheDocument();
  });
});
