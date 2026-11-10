import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Page } from "@/api/client";
import { writePoseMode, type PoseMode } from "@/lib/poseMode";
import { PosePanel, type PosePanelProps } from "./PosePanel";

const page = { id: 1 } as Page;
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => (resolve = r));
  return { promise, resolve };
}
/** 模式由呼叫端持有：這裡用 state 模擬 EditorPage，同時記錄 onModeChange。 */
function Host(props: PosePanelProps) {
  const [mode, setMode] = useState<PoseMode>(props.mode);
  return (
    <PosePanel
      {...props}
      mode={mode}
      onModeChange={(m) => {
        props.onModeChange(m);
        setMode(m);
      }}
    />
  );
}
function setup(over: Partial<PosePanelProps> = {}) {
  const props: PosePanelProps = {
    page,
    hasCharacter: true,
    characterHref: "/books/3/character",
    onGenerate: vi.fn(() => Promise.resolve()),
    generating: false,
    activeCount: 0,
    mode: "precise",
    onModeChange: vi.fn(),
    preciseUnavailableReason: null,
    ...over,
  };
  render(<Host {...props} />);
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

  it("presets append, then show an added, disabled state", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "揮手" }));
    await userEvent.click(screen.getByRole("button", { name: "看書" }));
    expect(box().value).toBe("waving one hand, reading a book");
    const added = screen.getByRole("button", { name: "揮手（已加入）" });
    expect(added).toBeDisabled();
    expect(added).not.toHaveAttribute("aria-pressed");
    expect(screen.getByRole("button", { name: "站著" })).toBeEnabled();
  });

  it("treats existing text case-insensitively and inside sentences as added", async () => {
    setup();
    await userEvent.type(box(), "Waving one hand and smiling");
    expect(screen.getByRole("button", { name: "揮手（已加入）" })).toBeDisabled();
    expect(box().value).toBe("Waving one hand and smiling");
  });

  it("disables presets that would exceed 300 characters, with a reason", async () => {
    setup();
    fireEvent.change(box(), { target: { value: "x".repeat(290) } });
    const b = screen.getByRole("button", { name: "站著" });
    expect(b).toBeEnabled();
    const long = screen.getByRole("button", { name: "拿著東西" });
    expect(long).toBeDisabled();
    expect(long).toHaveAttribute("title", expect.stringContaining("300"));
    expect(box().value).toHaveLength(290);
  });

  it("submits the trimmed action", async () => {
    const p = setup();
    await userEvent.type(box(), "  running fast ");
    await userEvent.click(gen());
    expect(p.onGenerate).toHaveBeenCalledWith("running fast", "precise");
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

  it("mode switch: 精準 selected by default with its hint", () => {
    setup();
    const group = screen.getByRole("radiogroup", { name: "產生方式" });
    expect(within(group).getByRole("radio", { name: "精準" })).toHaveAttribute("aria-checked", "true");
    expect(within(group).getByRole("radio", { name: "快速" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("約 30 秒，角色最像、動作最準")).toBeInTheDocument();
    expect(group).toHaveAccessibleDescription("約 30 秒，角色最像、動作最準");
  });

  it("clicking 快速 calls onModeChange and swaps the hint", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("radio", { name: "快速" }));
    expect(p.onModeChange).toHaveBeenCalledWith("fast");
    expect(screen.getByRole("radio", { name: "快速" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("約 10 秒，可能和角色有落差")).toBeInTheDocument();
    expect(screen.queryByText("約 30 秒，角色最像、動作最準")).not.toBeInTheDocument();
  });

  it("generating sends the current mode", async () => {
    const p = setup({ mode: "fast" });
    await userEvent.click(screen.getByRole("button", { name: "站著" }));
    await userEvent.click(gen());
    expect(p.onGenerate).toHaveBeenCalledWith("standing", "fast");
  });

  it("precise unavailable: 精準 disabled and the reason shown", async () => {
    const p = setup({ mode: "fast", preciseUnavailableReason: "精準模式缺少：ComfyUI-GGUF、qwen-image-edit-2511-Q4_K_M.gguf" });
    const precise = screen.getByRole("radio", { name: "精準" });
    expect(precise).toBeDisabled();
    expect(screen.getByRole("radio", { name: "快速" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("精準模式缺少：ComfyUI-GGUF、qwen-image-edit-2511-Q4_K_M.gguf")).toBeInTheDocument();
    await userEvent.click(precise);
    expect(p.onModeChange).not.toHaveBeenCalled();
  });

  it("caps at 300 characters", () => {
    setup();
    expect(box()).toHaveAttribute("maxlength", "300");
  });

  it("re-clicking the already selected 快速 (fallback) does not call onModeChange or write localStorage", async () => {
    localStorage.removeItem("coloring.poseMode");
    const onModeChange = vi.fn((m: PoseMode) => writePoseMode(m));
    setup({ mode: "fast", preciseUnavailableReason: "精準模式缺少：x", onModeChange });
    await userEvent.click(screen.getByRole("radio", { name: "快速" }));
    expect(onModeChange).not.toHaveBeenCalled();
    expect(localStorage.getItem("coloring.poseMode")).toBeNull();
  });
});
