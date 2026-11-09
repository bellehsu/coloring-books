import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Page } from "@/api/client";
import { Filmstrip, type FilmstripProps } from "./Filmstrip";

const page = (id: number, o: Partial<Page> = {}): Page =>
  ({
    id,
    book_id: 1,
    position: id - 1,
    scene_prompt: `場景 ${id}`,
    controlnet_strength: 0.4,
    sketch_url: null,
    selected_generation_id: null,
    status: "draft",
    line_detail: "standard",
    line_width_px: 11,
    character_check: null,
    lineart_url: null,
    needs_fix: null,
    aspect_mismatch: false,
    compose_pending: false,
    created_at: "",
    updated_at: "",
    ...o,
  }) as Page;

function setup(o: Partial<FilmstripProps> = {}) {
  const props: FilmstripProps = {
    pages: [
      page(1, { status: "passed", selected_generation_id: 3, lineart_url: "/l1.png" }),
      page(2, { needs_fix: true, selected_generation_id: 4 }),
      page(3),
      page(4),
    ],
    currentId: 1,
    busyPageIds: new Set([4]),
    paperAspect: [2551, 3579],
    onSelect: vi.fn(),
    onAdd: vi.fn(),
    onMove: vi.fn(),
    onDelete: vi.fn(),
    ...o,
  };
  render(<Filmstrip {...props} />);
  return props;
}

const pageButton = (n: number) => screen.getByRole("button", { name: new RegExp(`^第 ${n} 頁(?!的更多動作)`) });

describe("Filmstrip", () => {
  it("頁數、整本進度、狀態 Chip、縮圖", () => {
    setup();
    expect(screen.getByText("4 頁")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "整本進度" });
    expect(bar).toHaveAttribute("aria-valuenow", "1");
    expect(bar).toHaveAttribute("aria-valuemax", "4");
    expect(within(pageButton(1)).getByText("通過")).toBeInTheDocument();
    expect(within(pageButton(2)).getByText("需要修線")).toBeInTheDocument();
    expect(within(pageButton(3)).getByText("草稿")).toBeInTheDocument();
    expect(within(pageButton(4)).getByText("生成中")).toBeInTheDocument();
    expect(within(pageButton(2)).getByText("場景 2")).toBeInTheDocument();
    expect(pageButton(1).querySelector("img")).toHaveAttribute("src", "/l1.png");
    expect(pageButton(3).querySelector("img")).toBeNull();
  });

  it("目前頁 aria-current；點其他頁呼叫 onSelect", async () => {
    const p = setup();
    expect(pageButton(1)).toHaveAttribute("aria-current", "true");
    expect(pageButton(2)).not.toHaveAttribute("aria-current");
    await userEvent.click(pageButton(3));
    expect(p.onSelect).toHaveBeenCalledWith(3);
  });

  it("＋新增頁面", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("button", { name: "新增頁面" }));
    expect(p.onAdd).toHaveBeenCalled();
  });

  it("「⋯」選單：第一頁不能上移、最後一頁不能下移；下移呼叫 onMove", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("button", { name: "第 1 頁的更多動作" }));
    const menu = screen.getByRole("menu", { name: "第 1 頁" });
    expect(within(menu).getByRole("menuitem", { name: "上移" })).toBeDisabled();
    await userEvent.click(within(menu).getByRole("menuitem", { name: "下移" }));
    expect(p.onMove).toHaveBeenCalledWith(1, 1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "第 4 頁的更多動作" }));
    expect(within(screen.getByRole("menu")).getByRole("menuitem", { name: "下移" })).toBeDisabled();
  });

  it("選單可用鍵盤：開啟時聚焦第一個可用項目，Esc 關閉並回到按鈕", async () => {
    setup();
    const trigger = screen.getByRole("button", { name: "第 2 頁的更多動作" });
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menuitem", { name: "上移" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "下移" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("刪除需確認：取消不呼叫、確認後才呼叫 onDelete", async () => {
    const p = setup();
    await userEvent.click(screen.getByRole("button", { name: "第 3 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    const dlg = screen.getByRole("dialog", { name: "刪除第 3 頁？" });
    expect(p.onDelete).not.toHaveBeenCalled();
    await userEvent.click(within(dlg).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(p.onDelete).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "第 3 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    expect(p.onDelete).toHaveBeenCalledWith(3);
  });
});
