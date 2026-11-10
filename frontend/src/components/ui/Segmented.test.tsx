import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Segmented } from "./Segmented";

const opts = [
  { value: "a", label: "甲" },
  { value: "b", label: "乙", badge: 3 },
  { value: "c", label: "丙" },
] as const;

function Host({ kind }: { kind: "tabs" | "radio" }) {
  const [v, setV] = useState<"a" | "b" | "c">("a");
  return <Segmented kind={kind} label="模式" options={[...opts]} value={v} onChange={setV} />;
}

describe("Segmented", () => {
  it("tabs：aria-selected、方向鍵切換並移動焦點，只有選中者在 tab 順序", async () => {
    const user = userEvent.setup();
    render(<Host kind="tabs" />);
    expect(screen.getByRole("tablist", { name: "模式" })).toBeInTheDocument();
    const a = screen.getByRole("tab", { name: "甲" });
    expect(a).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /乙/ })).toHaveAttribute("tabindex", "-1");
    a.focus();
    await user.keyboard("{ArrowRight}");
    const b = screen.getByRole("tab", { name: /乙/ });
    expect(b).toHaveAttribute("aria-selected", "true");
    expect(b).toHaveFocus();
    expect(a).toHaveAttribute("aria-selected", "false");
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "丙" })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{Home}");
    expect(a).toHaveAttribute("aria-selected", "true");
  });

  it("badge 顯示", () => {
    render(<Host kind="tabs" />);
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("radio：radiogroup 與 aria-checked", async () => {
    const user = userEvent.setup();
    render(<Host kind="radio" />);
    expect(screen.getByRole("radiogroup", { name: "模式" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "丙" }));
    expect(screen.getByRole("radio", { name: "丙" })).toHaveAttribute("aria-checked", "true");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "甲" })).toHaveAttribute("aria-checked", "true");
  });

  it("disabled option: not clickable and skipped by arrow keys", async () => {
    const user = userEvent.setup();
    function H() {
      const [v, setV] = useState<"a" | "b" | "c">("a");
      return (
        <Segmented
          kind="radio"
          label="模式"
          options={[opts[0], { value: "b", label: "乙", disabled: true }, opts[2]]}
          value={v}
          onChange={setV}
        />
      );
    }
    render(<H />);
    const b = screen.getByRole("radio", { name: "乙" });
    expect(b).toBeDisabled();
    await user.click(b);
    expect(b).toHaveAttribute("aria-checked", "false");
    screen.getByRole("radio", { name: "甲" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("radio", { name: "丙" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "丙" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("radio", { name: "甲" })).toHaveAttribute("aria-checked", "true");
  });
});
