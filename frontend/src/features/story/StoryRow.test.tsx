import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Page } from "@/api/client";
import { StoryRow, type StoryRowProps } from "./StoryRow";

const page = (o: Partial<Page> = {}): Page =>
  ({
    id: 1,
    book_id: 1,
    position: 1,
    scene_prompt: "",
    action_prompt: "",
    background_generation_id: null,
    pose_generation_id: null,
    selected_generation_id: null,
    ...o,
  }) as Page;

function setup(o: Partial<StoryRowProps> = {}) {
  const onSave = vi.fn<StoryRowProps["onSave"]>(async () => ({}));
  const props = (p: Partial<StoryRowProps>): StoryRowProps => ({
    n: 1,
    page: page(),
    mode: "ai",
    onModeChange: vi.fn(),
    onUpload: vi.fn(),
    onSave,
    bg: { kind: "empty" },
    pose: { kind: "empty" },
    onRetry: vi.fn(),
    ...o,
    ...p,
  });
  const r = render(<StoryRow {...props({})} />);
  return { onSave, rerender: (p: Partial<StoryRowProps>) => r.rerender(<StoryRow {...props(p)} />), unmount: r.unmount };
}
const action = () => screen.getByRole("textbox", { name: "第 1 頁角色動作" });

afterEach(() => vi.useRealTimers());

describe("StoryRow", () => {
  it("存好之後同一段文字可以再存（伺服器值被別處改掉後再選同一個動作）", async () => {
    const { onSave, rerender } = setup();
    await userEvent.click(screen.getByRole("button", { name: "揮手" }));
    expect(onSave).toHaveBeenLastCalledWith({ action_prompt: "揮手" });
    await act(async () => {});
    rerender({ page: page({ action_prompt: "揮手" }) });
    rerender({ page: page({ action_prompt: "" }) }); // 例如別的分頁清掉了
    await userEvent.click(screen.getByRole("button", { name: "揮手" }));
    expect(onSave).toHaveBeenCalledTimes(2);
  });

  it("離開故事頁（卸載）時把還在等的輸入存起來", () => {
    vi.useFakeTimers();
    const { onSave, unmount } = setup();
    fireEvent.change(action(), { target: { value: "抱著書" } });
    expect(onSave).not.toHaveBeenCalled();
    unmount();
    expect(onSave).toHaveBeenCalledWith({ action_prompt: "抱著書" });
    vi.advanceTimersByTime(1000);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("常用動作：欄位空的時候填入，已有文字就用「、」接在後面", async () => {
    const { onSave, rerender } = setup();
    await userEvent.click(screen.getByRole("button", { name: "揮手" }));
    expect(action()).toHaveValue("揮手");
    rerender({ page: page({ action_prompt: "揮手" }) });
    await userEvent.click(screen.getByRole("button", { name: "跳起來" }));
    expect(action()).toHaveValue("揮手、跳起來");
    expect(onSave).toHaveBeenLastCalledWith({ action_prompt: "揮手、跳起來" });
  });

  it("上傳模式：AI 產生的背景不顯示「已上傳圖片」；選用的是上傳圖才顯示", () => {
    const { rerender } = setup({ mode: "upload", bg: { kind: "image", url: "/img/5.png" }, uploaded: false });
    expect(screen.getByText("選一張圖片當這頁的背景")).toBeInTheDocument();
    expect(screen.queryByText(/已上傳圖片/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "選擇圖片" })).toBeInTheDocument();
    rerender({ uploaded: true });
    expect(screen.getByText("已上傳圖片（已裁成頁面比例）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "換一張" })).toBeInTheDocument();
  });

  it("上傳模式：提示與構圖相同（含「需要多補線」），只接受 PNG／JPEG／WebP", () => {
    setup({ mode: "upload" });
    expect(screen.getByText("線條簡單的插畫效果最好；照片擷取的線稿可能很亂，需要多補線。")).toBeInTheDocument();
    expect(screen.getByLabelText("第 1 頁背景圖片")).toHaveAttribute("accept", "image/png,image/jpeg,image/webp");
  });
});
