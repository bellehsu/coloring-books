import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "./Button";
import { Chip } from "./Chip";
import { IconButton } from "./IconButton";
import { Icons } from "./Icons";

describe("ui basics", () => {
  it("Button 預設 type=button 並顯示圖示", () => {
    render(<Button icon={<Icons.plus />}>新增</Button>);
    const b = screen.getByRole("button", { name: "新增" });
    expect(b).toHaveAttribute("type", "button");
    expect(b.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
  it("IconButton 以 label 作為 aria-label", () => {
    render(<IconButton label="關閉"><Icons.close /></IconButton>);
    expect(screen.getByRole("button", { name: "關閉" })).toBeInTheDocument();
  });
  it("Chip 顯示文字", () => {
    render(<Chip tone="ok">已完成</Chip>);
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });
});
