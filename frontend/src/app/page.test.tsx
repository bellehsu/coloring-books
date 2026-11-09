import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Home from "@/app/page";

describe("首頁", () => {
  it("顯示「繪本工作室」", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { name: "繪本工作室" })).toBeInTheDocument();
  });
});
