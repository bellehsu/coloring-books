import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Placement } from "@/api/client";
import { ComposeStage, type ComposeStageProps } from "./ComposeStage";

const P: Placement = { cx: 0.5, cy: 0.5, height: 0.5, flip: false };

function setSize(img: HTMLElement, w: number, h: number) {
  Object.defineProperty(img, "naturalWidth", { configurable: true, value: w });
  Object.defineProperty(img, "naturalHeight", { configurable: true, value: h });
  fireEvent.load(img);
}

/** 背景 1000×800、去背角色 200×400（寬高比 0.5）；舞台在螢幕上 500×400 px。 */
function view(o: Partial<ComposeStageProps> = {}) {
  const onChange = vi.fn();
  const props: ComposeStageProps = {
    backgroundUrl: "/bg.png",
    cutoutUrl: "/cutout.png",
    placement: P,
    onChange,
    ...o,
  };
  const r = render(<ComposeStage {...props} />);
  const load = () => {
    setSize(screen.getByRole("img", { name: "背景圖" }), 1000, 800);
    setSize(screen.getByTestId("cutout"), 200, 400);
    const overlay = screen.getByTestId("compose-overlay");
    overlay.getBoundingClientRect = () => ({ left: 0, top: 0, width: 500, height: 400, right: 500, bottom: 400, x: 0, y: 0, toJSON() {} });
  };
  const rerender = (n: Partial<ComposeStageProps>) => r.rerender(<ComposeStage {...props} {...n} />);
  return { onChange, load, rerender };
}

const box = () => screen.getByRole("group", { name: /角色位置/ });

describe("ComposeStage", () => {
  it("沒有背景圖網址 → 說明要重新產生，不是一直載入中", () => {
    view({ backgroundUrl: null });
    expect(screen.getByRole("alert")).toHaveTextContent("背景圖片無法載入，請重新產生");
    expect(screen.queryByText("載入中…")).toBeNull();
  });

  it("沒有去背圖（角色動作缺遮罩）→ 說明要重新產生", () => {
    view({ cutoutUrl: null });
    expect(screen.getByRole("alert")).toHaveTextContent("角色動作缺少去背遮罩，請重新產生");
  });

  it("背景圖或去背圖載入失敗 → 說明", () => {
    view();
    fireEvent.error(screen.getByRole("img", { name: "背景圖" }));
    expect(screen.getByRole("alert")).toHaveTextContent("背景圖片無法載入，請重新產生");
  });

  it("去背圖載入失敗 → 說明", () => {
    view();
    fireEvent.error(screen.getByTestId("cutout"));
    expect(screen.getByRole("alert")).toHaveTextContent("角色去背圖無法載入，請重新產生");
  });

  it("載入前角色框隱藏；載入後依位置與去背圖的實際比例擺放（百分比，跟著舞台寬度縮放）", () => {
    const { load } = view();
    expect(screen.getByText("載入中…")).toBeInTheDocument();
    load();
    expect(screen.queryByText("載入中…")).toBeNull();
    // 背景 1000×800：高 0.5 → 400 px、寬 200 px；中心 (500, 400) → left 400/1000、top 200/800
    expect(box().style.left).toBe("40%");
    expect(box().style.top).toBe("25%");
    expect(box().style.width).toBe("20%");
    expect(box().style.height).toBe("50%");
    expect(screen.getByTestId("cutout")).toHaveAttribute("src", "/cutout.png");
  });

  it("翻轉時角色圖鏡像", () => {
    const { load } = view({ placement: { ...P, flip: true } });
    load();
    expect(screen.getByTestId("cutout")).toHaveAttribute("data-flip", "true");
  });

  it("拖曳角色：移動中只更新畫面，放開時 onChange 一次，cx 增加", () => {
    const { load, onChange } = view();
    load();
    fireEvent.pointerDown(box(), { pointerId: 1, button: 0, clientX: 250, clientY: 200 });
    fireEvent.pointerMove(box(), { pointerId: 1, clientX: 300, clientY: 200 });
    expect(onChange).not.toHaveBeenCalled();
    expect(box().style.left).toBe("50%"); // cx 0.6 → (600 − 100)/1000
    fireEvent.pointerUp(box(), { pointerId: 1, clientX: 300, clientY: 200 });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toEqual({ ...P, cx: 0.6 });
  });

  it("只點一下不移動 → 不送 onChange", () => {
    const { load, onChange } = view();
    load();
    fireEvent.pointerDown(box(), { pointerId: 1, button: 0, clientX: 250, clientY: 200 });
    fireEvent.pointerUp(box(), { pointerId: 1, clientX: 250, clientY: 200 });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("拖右下角把手：左上角不動、等比放大", () => {
    const { load, onChange } = view();
    load();
    const handle = screen.getByTestId("resize-handle");
    fireEvent.pointerDown(handle, { pointerId: 2, button: 0, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 300, clientY: 340 }); // 往下 40/400 = 0.1
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 300, clientY: 340 });
    expect(onChange).toHaveBeenCalledTimes(1);
    const p = onChange.mock.calls[0][0] as Placement;
    expect(p.height).toBeCloseTo(0.6);
    expect(p.cy - p.height / 2).toBeCloseTo(0.25); // 上緣不動
  });

  it("鍵盤：方向鍵 1%、Shift＋方向鍵 5%、+／- 縮放 5%，每次按鍵 onChange", () => {
    const { load, onChange } = view();
    load();
    box().focus();
    expect(box()).toHaveFocus();
    fireEvent.keyDown(box(), { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith({ ...P, cx: 0.51 });
    fireEvent.keyDown(box(), { key: "ArrowUp", shiftKey: true });
    expect(onChange).toHaveBeenLastCalledWith({ ...P, cy: 0.45 });
    fireEvent.keyDown(box(), { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith({ ...P, cx: 0.49 });
    fireEvent.keyDown(box(), { key: "ArrowDown", shiftKey: true });
    expect(onChange).toHaveBeenLastCalledWith({ ...P, cy: 0.55 });
    fireEvent.keyDown(box(), { key: "+" });
    expect(onChange).toHaveBeenLastCalledWith({ ...P, height: 0.55 });
    fireEvent.keyDown(box(), { key: "-" });
    expect(onChange).toHaveBeenLastCalledWith({ ...P, height: 0.45 });
    fireEvent.keyDown(box(), { key: "a" });
    expect(onChange).toHaveBeenCalledTimes(6);
  });

  it("disabled（確認合成中）：拖曳與按鍵都不改位置", () => {
    const { load, onChange } = view({ disabled: true });
    load();
    fireEvent.keyDown(box(), { key: "ArrowRight" });
    fireEvent.pointerDown(box(), { pointerId: 1, button: 0, clientX: 250, clientY: 200 });
    fireEvent.pointerMove(box(), { pointerId: 1, clientX: 300, clientY: 200 });
    fireEvent.pointerUp(box(), { pointerId: 1, clientX: 300, clientY: 200 });
    const handle = screen.getByTestId("resize-handle");
    fireEvent.pointerDown(handle, { pointerId: 2, button: 0, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(handle, { pointerId: 2, clientX: 300, clientY: 340 });
    fireEvent.pointerUp(handle, { pointerId: 2, clientX: 300, clientY: 340 });
    expect(onChange).not.toHaveBeenCalled();
    expect(box().style.left).toBe("40%");
    expect(box()).toHaveAttribute("aria-disabled", "true");
  });

  it("拖到一半開始確認合成 → 拖曳取消、畫面回到已存的位置（合成用的就是它），放開也不送", () => {
    const { load, onChange, rerender } = view();
    load();
    fireEvent.pointerDown(box(), { pointerId: 1, button: 0, clientX: 250, clientY: 200 });
    fireEvent.pointerMove(box(), { pointerId: 1, clientX: 300, clientY: 200 });
    expect(box().style.left).toBe("50%");
    rerender({ disabled: true });
    expect(box().style.left).toBe("40%");
    fireEvent.pointerMove(box(), { pointerId: 1, clientX: 350, clientY: 200 });
    fireEvent.pointerUp(box(), { pointerId: 1, clientX: 350, clientY: 200 });
    expect(onChange).not.toHaveBeenCalled();
    expect(box().style.left).toBe("40%");
  });

  it("位置從外部改變（翻轉、重設）時畫面跟著變", () => {
    const { load, rerender } = view();
    load();
    rerender({ placement: { ...P, cx: 0.3 } });
    expect(box().style.left).toBe("20%");
  });

  it("Ctrl／⌘／Alt＋按鍵（瀏覽器縮放等）不移動、不縮放角色", () => {
    const { load, onChange } = view();
    load();
    fireEvent.keyDown(box(), { key: "-", ctrlKey: true });
    fireEvent.keyDown(box(), { key: "=", metaKey: true });
    fireEvent.keyDown(box(), { key: "ArrowLeft", altKey: true });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("失去 pointer capture（沒有 pointerup）→ 取消拖曳，之後還能再拖", () => {
    const { load, onChange } = view();
    load();
    fireEvent.pointerDown(box(), { pointerId: 1, button: 0, clientX: 250, clientY: 200 });
    fireEvent.pointerMove(box(), { pointerId: 1, clientX: 300, clientY: 200 });
    fireEvent.lostPointerCapture(box(), { pointerId: 1 });
    expect(box().style.left).toBe("40%"); // 回到已存的位置
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.pointerDown(box(), { pointerId: 3, button: 0, clientX: 250, clientY: 200 });
    fireEvent.pointerUp(box(), { pointerId: 3, clientX: 300, clientY: 200 });
    expect(onChange).toHaveBeenCalledWith({ ...P, cx: 0.6 });
  });

  it("舞台寬度帶背景寬高比（CSS 用來讓整張背景塞進可視高度）", () => {
    const { load } = view();
    load();
    const frame = screen.getByTestId("compose-overlay").parentElement!;
    expect(frame.style.getPropertyValue("--bg-aspect")).toBe("1.25");
  });
});
