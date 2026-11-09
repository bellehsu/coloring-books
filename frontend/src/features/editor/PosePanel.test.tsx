import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Page } from "@/api/client";
import { PosePanel, type PosePanelProps } from "./PosePanel";

const page = { id: 1 } as Page;
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}
function setup(over: Partial<PosePanelProps> = {}) {
  const props: PosePanelProps = {
    page,
    hasCharacter: true,
    characterHref: "/books/3/character",
    onGenerate: vi.fn(() => Promise.resolve()),
    generating: false,
    activeCount: 0,
    ...over,
  };
  render(<PosePanel {...props} />);
  return props;
}
const gen = () => screen.getByRole("button", { name: "產生角色動作" });
const box = () => screen.getByLabelText("動作描述") as HTMLTextAreaElement;

describe("PosePanel", () => {
  it("is disabled while the description is empty or blank", async () => {
    setup();
    expect(gen()).toBeDisabled();
    await userEvent.type(box(), "   ");
    expect(gen()).toBeDisabled();
  });

  it("presets append, do not duplicate, and reflect aria-pressed", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "揮手" }));
    await userEvent.click(screen.getByRole("button", { name: "看書" }));
    expect(box().value).toBe("waving one hand, reading a book");
    await userEvent.click(screen.getByRole("button", { name: "揮手" }));
    expect(box().value).toBe("waving one hand, reading a book");
    expect(screen.getByRole("button", { name: "揮手" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "站著" })).toHaveAttribute("aria-pressed", "false");
  });

  it("submits the trimmed action", async () => {
    const p = setup();
    await userEvent.type(box(), "  running fast ");
    await userEvent.click(gen());
    expect(p.onGenerate).toHaveBeenCalledWith("running fast");
  });

  it("guards double clicks until the call settles", async () => {
    const d = deferred();
    const p = setup({ onGenerate: vi.fn(() => d.promise) });
    await userEvent.click(screen.getByRole("button", { name: "站著" }));
    await userEvent.dblClick(gen());
    expect(p.onGenerate).toHaveBeenCalledTimes(1);
    expect(gen()).toBeDisabled();
    d.resolve();
    await vi.waitFor(() => expect(gen()).toBeEnabled());
  });

  it("is disabled while generating", async () => {
    setup({ generating: true });
    await userEvent.type(box(), "x");
    expect(gen()).toBeDisabled();
  });

  it("without a character disables and links to the studio", () => {
    setup({ hasCharacter: false });
    expect(gen()).toBeDisabled();
    expect(screen.getByRole("link", { name: "先設定角色" })).toHaveAttribute("href", "/books/3/character");
  });

  it("shows the queued count", () => {
    setup({ activeCount: 2 });
    expect(screen.getByRole("status")).toHaveTextContent("本頁 2 張排隊或生成中");
  });

  it("caps at 300 characters", () => {
    setup();
    expect(box()).toHaveAttribute("maxlength", "300");
  });
});
