import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mkPage } from "@/features/fix/testData";
import { CharacterToggle } from "./CharacterToggle";

function setup(o: { page?: Parameters<typeof mkPage>[0]; uploaded?: boolean; busy?: boolean } = {}) {
  const onToggle = vi.fn(async () => {});
  render(<CharacterToggle page={mkPage(o.page)} backgroundUploaded={!!o.uploaded} busy={!!o.busy} onToggle={onToggle} />);
  return onToggle;
}

describe("CharacterToggle", () => {
  it("取消勾選直接送 include=false、redraw_background=true", async () => {
    const onToggle = setup();
    await userEvent.click(screen.getByRole("checkbox", { name: "放主角" }));
    expect(onToggle).toHaveBeenCalledWith({ include: false, redraw_background: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("勾回開小選擇：兩個選項、預設焦點在重畫背景、有可用動作時 1 次／0 次", async () => {
    const onToggle = setup({ page: { include_character: false, pose_generation_id: 20 } });
    await userEvent.click(screen.getByRole("checkbox", { name: "放主角" }));
    const dlg = screen.getByRole("dialog");
    const redraw = within(dlg).getByRole("button", { name: /重畫背景（恐龍移到旁邊）/ });
    const keep = within(dlg).getByRole("button", { name: /保留背景，主角我自己調位置/ });
    expect(redraw).toHaveTextContent("用 1 次");
    expect(keep).toHaveTextContent("用 0 次");
    await waitFor(() => expect(redraw).toHaveFocus());
    await userEvent.click(keep);
    expect(onToggle).toHaveBeenCalledWith({ include: true, redraw_background: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("沒有可用動作：重畫 2 次、保留 1 次；選重畫送 redraw_background=true", async () => {
    const onToggle = setup({ page: { include_character: false, pose_generation_id: null } });
    await userEvent.click(screen.getByRole("checkbox", { name: "放主角" }));
    const dlg = screen.getByRole("dialog");
    expect(within(dlg).getByRole("button", { name: /重畫背景/ })).toHaveTextContent("用 2 次");
    expect(within(dlg).getByRole("button", { name: /保留背景/ })).toHaveTextContent("用 1 次");
    await userEvent.click(within(dlg).getByRole("button", { name: /重畫背景/ }));
    expect(onToggle).toHaveBeenCalledWith({ include: true, redraw_background: true });
  });

  it("上傳的背景只有保留選項", async () => {
    setup({ page: { include_character: false }, uploaded: true });
    await userEvent.click(screen.getByRole("checkbox", { name: "放主角" }));
    const dlg = screen.getByRole("dialog");
    expect(within(dlg).queryByRole("button", { name: /重畫背景/ })).toBeNull();
    expect(within(dlg).getByRole("button", { name: /保留背景/ })).toBeInTheDocument();
  });

  it("相框頁停用並說明", () => {
    setup({ page: { placement: { cx: 0.5, cy: 0.5, scale: 1, flip: false, frame: { x: 1, y: 1 } } as never } });
    expect(screen.getByRole("checkbox", { name: "放主角" })).toBeDisabled();
    expect(screen.getByText("相框頁要放主角")).toBeInTheDocument();
  });

  it("切換中停用並顯示「切換中…」", () => {
    setup({ page: { character_pending: true } });
    expect(screen.getByRole("checkbox", { name: "放主角" })).toBeDisabled();
    expect(screen.getByText("切換中…")).toBeInTheDocument();
  });
});
