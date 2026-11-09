import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Generation } from "@/api/client";
import { CandidateTray } from "./CandidateTray";

const gen = (id: number, o: Partial<Generation> = {}): Generation =>
  ({
    id,
    book_id: 1,
    page_id: 1,
    kind: "page",
    status: "succeeded",
    discarded: false,
    prompt: "",
    user_text: "",
    seed: 100 + id,
    width: 864,
    height: 1216,
    image_url: `/img/${id}.png`,
    error: null,
    elapsed_s: 3,
    queue_position: null,
    progress: null,
    created_at: "",
    ...o,
  }) as Generation;

function Harness({ gens, onSelect }: { gens: Generation[]; onSelect: (g: Generation) => void }) {
  const [sel, setSel] = useState<number | null>(null);
  return (
    <CandidateTray
      generations={gens}
      selectedId={sel}
      onSelect={(g) => {
        onSelect(g);
        setSel(g.id);
      }}
      onToggleDiscard={vi.fn()}
      onCancel={vi.fn()}
    />
  );
}

describe("CandidateTray", () => {
  it("新的在前；點選呼叫 onSelect，選中顯示「使用中」", async () => {
    const onSelect = vi.fn();
    render(<Harness gens={[gen(1), gen(2), gen(3)]} onSelect={onSelect} />);
    const picks = screen.getAllByRole("button", { name: /^選 seed/ });
    expect(picks.map((b) => b.getAttribute("aria-label"))).toEqual([
      "選 seed 103 為本頁圖片",
      "選 seed 102 為本頁圖片",
      "選 seed 101 為本頁圖片",
    ]);
    expect(screen.queryByText("使用中")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "選 seed 102 為本頁圖片" }));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
    const btn = screen.getByRole("button", { name: "選 seed 102 為本頁圖片" });
    expect(btn).toHaveAttribute("aria-pressed", "true");
    expect(within(btn).getByText("使用中")).toBeInTheDocument();
  });

  it("排隊／生成中／失敗／已取消；排隊中可取消", async () => {
    const onCancel = vi.fn();
    render(
      <CandidateTray
        generations={[
          gen(1, { status: "failed", image_url: null, error: "ComfyUI 無法連線" }),
          gen(2, { status: "cancelled", image_url: null }),
          gen(3, { status: "running", image_url: null, queue_position: 0, progress: [5, 20] }),
          gen(4, { status: "queued", image_url: null, queue_position: 2 }),
        ]}
        selectedId={null}
        onSelect={vi.fn()}
        onToggleDiscard={vi.fn()}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByText("ComfyUI 無法連線")).toBeInTheDocument();
    expect(screen.getByText("已取消")).toBeInTheDocument();
    expect(screen.getByText("生成中")).toBeInTheDocument();
    expect(screen.getByText("排隊第 2 位")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "生成進度" });
    expect(bar).toHaveAttribute("aria-valuenow", "5");
    expect(bar).toHaveAttribute("aria-valuemax", "20");
    expect(screen.queryByRole("button", { name: /^選 seed/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "取消 seed 104" }));
    expect(onCancel).toHaveBeenCalledWith(expect.objectContaining({ id: 4 }));
  });

  it("捨棄的預設隱藏；「歷史 N」切換顯示，可還原", async () => {
    const onToggle = vi.fn();
    render(
      <CandidateTray
        generations={[gen(1, { discarded: true }), gen(2)]}
        selectedId={null}
        onSelect={vi.fn()}
        onToggleDiscard={onToggle}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "選 seed 101 為本頁圖片" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "捨棄 seed 102" }));
    expect(onToggle).toHaveBeenLastCalledWith(expect.objectContaining({ id: 2 }));
    const hist = screen.getByRole("button", { name: /歷史 1/ });
    expect(hist).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(hist);
    expect(hist).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "還原 seed 101" }));
    expect(onToggle).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1 }));
  });

  it("沒有候選時顯示提示", () => {
    render(
      <CandidateTray generations={[]} selectedId={null} onSelect={vi.fn()} onToggleDiscard={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByText(/還沒有候選圖/)).toBeInTheDocument();
  });
});
