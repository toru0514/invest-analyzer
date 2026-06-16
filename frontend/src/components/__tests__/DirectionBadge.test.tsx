import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import DirectionBadge from "@/components/DirectionBadge";

describe("DirectionBadge", () => {
  it("buy は『買い』を緑系で表示する", () => {
    render(<DirectionBadge direction="buy" />);
    const el = screen.getByText("買い");
    expect(el).toBeInTheDocument();
    expect(el.className).toContain("green");
  });

  it("sell は『売り』を赤系で表示する", () => {
    render(<DirectionBadge direction="sell" />);
    const el = screen.getByText("売り");
    expect(el.className).toContain("red");
  });

  it("neutral は『中立』を表示する", () => {
    render(<DirectionBadge direction="neutral" />);
    expect(screen.getByText("中立")).toBeInTheDocument();
  });
});
