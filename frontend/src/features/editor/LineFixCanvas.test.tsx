import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import type { Stroke } from "@/lib/strokes";
import { LineFixCanvas, ZoomControls, type LineFixCanvasProps } from "./LineFixCanvas";

/**
 * jsdom 沒有 canvas：假 2D context 只記錄呼叫。紙張的 getBoundingClientRect 固定成
 * 「1 個螢幕 px＝1 個內容 px」（W×H），pointer 座標就能精確換算。
 */
const W = 101;
const H = 201;
let calls: [string, ...unknown[]][];

function makeCtx() {
  const ctx = {
    strokeStyle: "" as unknown,
    fillStyle: "" as unknown,
    lineWidth: 0,
    lineCap: "butt",
    lineJoin: "miter",
    save: () => calls.push(["save"]),
    restore: () => calls.push(["restore"]),
    setTransform: (...a: number[]) => calls.push(["setTransform", ...a]),
    clearRect: () => calls.push(["clearRect"]),
    beginPath: () => calls.push(["beginPath"]),
    moveTo: (x: number, y: number) => calls.push(["moveTo", x, y]),
    lineTo: (x: number, y: number) => calls.push(["lineTo", x, y]),
    arc: (x: number, y: number, r: number) => calls.push(["arc", x, y, r]),
    stroke: () => calls.push(["stroke", ctx.strokeStyle, ctx.lineWidth]),
    fill: () => calls.push(["fill", ctx.fillStyle]),
  };
  return ctx;
}

let paperRect: DOMRect;

beforeEach(() => {
  calls = [];
  const ctx = makeCtx();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => ctx as unknown as CanvasRenderingContext2D,
  );
  paperRect = { left: 0, top: 0, width: W, height: H, right: W, bottom: H, x: 0, y: 0 } as DOMRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.dataset.part === "paper") return paperRect;
    return { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0, x: 0, y: 0 } as DOMRect;
  });
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
});

afterEach(() => {
  vi.restoreAllMocks();
});

const baseProps = (over: Partial<LineFixCanvasProps> = {}): LineFixCanvasProps => ({
  lineartUrl: "/files/lineart.png?v=1",
  contentPx: [W, H],
  gaps: [],
  strokes: [],
  onChange: vi.fn(),
  lineWidthPx: 11,
  ...over,
});

function setup(over: Partial<LineFixCanvasProps> = {}) {
  const props = baseProps(over);
  const utils = render(
    <ToastProvider>
      <LineFixCanvas {...props} />
    </ToastProvider>,
  );
  const surface = utils.container.querySelector("canvas")!;
  const paper = utils.container.querySelector<HTMLElement>('[data-part="paper"]')!;
  return { ...utils, props, surface, paper, onChange: props.onChange as ReturnType<typeof vi.fn> };
}

/** 在內容像素 (px,py) → 螢幕座標（像素中心）：client = px + 0.5。 */
const at = (px: number, py: number) => ({ clientX: px + 0.5, clientY: py + 0.5 });

function drag(el: Element, from: [number, number], to: [number, number], extra: Record<string, unknown> = {}) {
  fireEvent.pointerDown(el, { pointerId: 1, button: 0, ...at(...from), ...extra });
  fireEvent.pointerMove(el, { pointerId: 1, ...at((from[0] + to[0]) / 2, (from[1] + to[1]) / 2), ...extra });
  fireEvent.pointerMove(el, { pointerId: 1, ...at(...to), ...extra });
  fireEvent.pointerUp(el, { pointerId: 1, ...at(...to), ...extra });
}

describe("LineFixCanvas", () => {
  it("修線工具列：補線（預設）、擦除、復原（自動補線在右欄，不在工具列）", () => {
    setup();
    const bar = screen.getByRole("toolbar", { name: "修線工具" });
    const draw = within(bar).getByRole("button", { name: "補線" });
    const erase = within(bar).getByRole("button", { name: "擦除" });
    expect(draw).toHaveAttribute("aria-pressed", "true");
    expect(erase).toHaveAttribute("aria-pressed", "false");
    expect(within(bar).getByRole("button", { name: "復原" })).toBeDisabled();
    expect(within(bar).queryByRole("button", { name: "自動補線" })).not.toBeInTheDocument();
    fireEvent.click(erase);
    expect(erase).toHaveAttribute("aria-pressed", "true");
  });

  it("線稿圖片用 lineartUrl；紙張比例＝contentPx", () => {
    const { container, paper } = setup();
    const img = container.querySelector("img")!;
    expect(img).toHaveAttribute("src", "/files/lineart.png?v=1");
    expect(img).toHaveAttribute("alt", expect.stringContaining("線稿"));
    const w = parseFloat(paper.style.width);
    const h = parseFloat(paper.style.height);
    expect(w / h).toBeCloseTo(W / H, 3);
  });

  it("拖曳一筆 → onChange 收到 1 筆，op=draw、寬＝lineWidthPx、座標在 0–1 且對齊 px/(w-1)", () => {
    const { surface, onChange } = setup();
    drag(surface, [0, 0], [100, 200]);
    expect(onChange).toHaveBeenCalledTimes(1);
    const strokes = onChange.mock.calls[0][0] as Stroke[];
    expect(strokes).toHaveLength(1);
    const [s] = strokes;
    expect(s.op).toBe("draw");
    expect(s.width_px).toBe(11);
    for (const [x, y] of s.points) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(1);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(1);
    }
    expect(s.points[0][0]).toBeCloseTo(0, 6);
    expect(s.points[0][1]).toBeCloseTo(0, 6);
    expect(s.points[s.points.length - 1][0]).toBeCloseTo(1, 6);
    expect(s.points[s.points.length - 1][1]).toBeCloseTo(1, 6);
  });

  it("拖曳時立即畫在疊層 canvas（不等伺服器）", () => {
    const { surface } = setup();
    calls = [];
    fireEvent.pointerDown(surface, { pointerId: 1, button: 0, ...at(10, 10) });
    fireEvent.pointerMove(surface, { pointerId: 1, ...at(40, 40) });
    expect(calls.some((c) => c[0] === "stroke" && c[1] === "#000000" && c[2] === 11)).toBe(true);
  });

  it("縮放後座標仍正確（紙張 rect 放大兩倍、平移）", () => {
    paperRect = { left: 30, top: 20, width: W * 2, height: H * 2, right: 30 + W * 2, bottom: 20 + H * 2, x: 30, y: 20 } as DOMRect;
    const { surface, onChange } = setup();
    // 內容像素 50 的中心在 rect 內 (50.5/W)*2W = 101
    fireEvent.pointerDown(surface, { pointerId: 1, button: 0, clientX: 30 + 101, clientY: 20 + 201 });
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 30 + 101, clientY: 20 + 201 });
    const [s] = onChange.mock.calls[0][0] as Stroke[];
    expect(s.points).toHaveLength(1);
    expect(s.points[0][0]).toBeCloseTo(50 / (W - 1), 6);
    expect(s.points[0][1]).toBeCloseTo(100 / (H - 1), 6);
  });

  it("擦除：op=erase、寬 24（300 DPI px）", () => {
    const { surface, onChange } = setup();
    fireEvent.click(screen.getByRole("button", { name: "擦除" }));
    drag(surface, [10, 10], [50, 50]);
    const [s] = onChange.mock.calls[0][0] as Stroke[];
    expect(s.op).toBe("erase");
    expect(s.width_px).toBe(24);
  });

  it("補線寬夾在後端 2–64", () => {
    const { surface, onChange } = setup({ lineWidthPx: 120 });
    drag(surface, [10, 10], [50, 50]);
    expect((onChange.mock.calls[0][0] as Stroke[])[0].width_px).toBe(64);
  });

  it("只點一下 → 單點筆畫", () => {
    const { surface, onChange } = setup();
    fireEvent.pointerDown(surface, { pointerId: 1, button: 0, ...at(50, 100) });
    fireEvent.pointerUp(surface, { pointerId: 1, ...at(50, 100) });
    const [s] = onChange.mock.calls[0][0] as Stroke[];
    expect(s.points).toEqual([[0.5, 0.5]]);
  });

  it("連續快畫三筆（受控 strokes）→ 最後一次 onChange 有三筆，順序不變", () => {
    const seen: Stroke[][] = [];
    function Host() {
      const [strokes, setStrokes] = useState<Stroke[]>([]);
      return (
        <LineFixCanvas
          {...baseProps({
            strokes,
            onChange: (s) => {
              seen.push(s);
              setStrokes(s);
            },
          })}
        />
      );
    }
    const { container } = render(
      <ToastProvider>
        <Host />
      </ToastProvider>,
    );
    const surface = container.querySelector("canvas")!;
    drag(surface, [10, 10], [20, 10]);
    drag(surface, [10, 30], [20, 30]);
    drag(surface, [10, 50], [20, 50]);
    expect(seen).toHaveLength(3);
    expect(seen[2]).toHaveLength(3);
    const ys = seen[2].map((s) => s.points[0][1]);
    [10, 30, 50].forEach((py, i) => expect(ys[i]).toBeCloseTo(py / (H - 1), 9));
  });

  it("父層還沒重繪就來第二筆，也不會掉第一筆", () => {
    const { surface, onChange } = setup();
    act(() => {
      drag(surface, [10, 10], [20, 10]);
      drag(surface, [10, 30], [20, 30]);
    });
    expect((onChange.mock.calls[1][0] as Stroke[]).length).toBe(2);
  });

  it("失去指標捕捉（lostpointercapture）→ 結束這一筆，之後的 pointerdown 仍能畫", () => {
    const { surface, onChange } = setup();
    fireEvent.pointerDown(surface, { pointerId: 1, button: 0, ...at(10, 10) });
    fireEvent.pointerMove(surface, { pointerId: 1, ...at(40, 10) });
    fireEvent.lostPointerCapture(surface, { pointerId: 1 });
    expect(onChange).toHaveBeenCalledTimes(1); // 跟 pointercancel 一樣提交
    drag(surface, [10, 50], [40, 50], { pointerId: 2 });
    expect(onChange).toHaveBeenCalledTimes(2);
    expect((onChange.mock.calls[1][0] as Stroke[]).length).toBe(2);
  });

  it("正常放開後的 lostpointercapture 不會重複提交", () => {
    const { surface, onChange } = setup();
    drag(surface, [10, 10], [40, 10]);
    fireEvent.lostPointerCapture(surface, { pointerId: 1 });
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("復原＝移除最後一筆", () => {
    const strokes: Stroke[] = [
      { op: "draw", width_px: 11, points: [[0.1, 0.1]] },
      { op: "erase", width_px: 24, points: [[0.2, 0.2]] },
    ];
    const { onChange } = setup({ strokes });
    const undo = screen.getByRole("button", { name: "復原" });
    expect(undo).toBeEnabled();
    fireEvent.click(undo);
    expect(onChange).toHaveBeenCalledWith([strokes[0]]);
  });

  it("既有 strokes 會畫到疊層（補線黑、擦除白）", () => {
    setup({
      strokes: [
        { op: "draw", width_px: 11, points: [[0, 0], [1, 1]] },
        { op: "erase", width_px: 24, points: [[0.5, 0.5], [1, 0]] },
      ],
    });
    expect(calls).toContainEqual(["moveTo", 0.5, 0.5]);
    expect(calls).toContainEqual(["lineTo", 100.5, 200.5]);
    expect(calls).toContainEqual(["stroke", "#000000", 11]);
    expect(calls).toContainEqual(["stroke", "#ffffff", 24]);
  });

  it("缺口橘色圈：位置用相對座標（%），直徑 30 px 螢幕尺寸；狀態列顯示數量", () => {
    const { container } = setup({
      gaps: [
        [0.25, 0.5],
        [0.75, 0.1],
        [0.5, 0.9],
      ],
    });
    const rings = container.querySelectorAll<HTMLElement>('[data-part="gap"]');
    expect(rings).toHaveLength(3);
    expect(rings[0].style.left).toBe("25%");
    expect(rings[0].style.top).toBe("50%");
    expect(screen.getByRole("status")).toHaveTextContent("3 個缺口");
  });

  it("沒有缺口 → 狀態列「沒有缺口」；pending 時顯示更新中", () => {
    const { rerender } = setup();
    expect(screen.getByRole("status")).toHaveTextContent("沒有缺口");
    rerender(
      <ToastProvider>
        <LineFixCanvas {...baseProps({ pending: true })} />
      </ToastProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("更新中");
  });

  it("statusOverride（例如寫入失敗＋重試）取代缺口狀態列，優先於 pending", () => {
    setup({
      gaps: [[0.5, 0.5]],
      pending: true,
      statusOverride: (
        <span>
          修線尚未儲存 <button type="button">重試</button>
        </span>
      ),
    });
    expect(screen.queryByText(/個缺口/)).not.toBeInTheDocument();
    expect(screen.queryByText("更新中…")).not.toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("修線尚未儲存");
    expect(within(alert).getByRole("button", { name: "重試" })).toBeInTheDocument();
  });

  it("中鍵拖曳＝平移，不產生筆畫", () => {
    const { surface, onChange } = setup();
    drag(surface, [10, 10], [50, 50], { button: 1 });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("按住空白鍵拖曳＝平移，不產生筆畫；放開空白鍵後恢復畫線", () => {
    const { surface, onChange } = setup();
    fireEvent.keyDown(window, { key: " ", code: "Space" });
    drag(surface, [10, 10], [50, 50]);
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.keyUp(window, { key: " ", code: "Space" });
    drag(surface, [10, 10], [50, 50]);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("放大後中鍵拖曳會移動紙張", () => {
    const { surface, paper } = setup({ zoom: 4, onZoomChange: vi.fn() });
    const before = paper.style.transform;
    fireEvent.pointerDown(surface, { pointerId: 1, button: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(surface, { pointerId: 1, clientX: 160, clientY: 140 });
    fireEvent.pointerUp(surface, { pointerId: 1, clientX: 160, clientY: 140 });
    expect(paper.style.transform).not.toBe(before);
  });

  it("超過後端上限（500 筆）→ 不送出，顯示錯誤", () => {
    const strokes: Stroke[] = Array.from({ length: 500 }, () => ({ op: "draw", width_px: 11, points: [[0.1, 0.1]] }));
    const { surface, onChange } = setup({ strokes });
    drag(surface, [10, 10], [50, 50]);
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/500/);
  });

  it("非受控縮放：工具列有 縮小／放大，50%–400%", () => {
    setup();
    const out = screen.getByRole("button", { name: "縮小" });
    const inn = screen.getByRole("button", { name: "放大" });
    expect(screen.getByText("100%")).toBeInTheDocument();
    fireEvent.click(inn);
    expect(screen.getByText("150%")).toBeInTheDocument();
    for (let i = 0; i < 10; i++) fireEvent.click(inn);
    expect(screen.getByText("400%")).toBeInTheDocument();
    expect(inn).toBeDisabled();
    for (let i = 0; i < 10; i++) fireEvent.click(out);
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(out).toBeDisabled();
  });

  it("受控縮放：不顯示內建縮放按鈕；Ctrl＋滾輪呼叫 onZoomChange（夾在範圍內）", () => {
    const onZoomChange = vi.fn();
    const { container } = setup({ zoom: 1, onZoomChange });
    expect(screen.queryByRole("button", { name: "放大" })).toBeNull();
    const viewport = container.querySelector('[data-part="viewport"]')!;
    fireEvent.wheel(viewport, { deltaY: -200, ctrlKey: true, clientX: 50, clientY: 50 });
    expect(onZoomChange).toHaveBeenCalledTimes(1);
    const z = onZoomChange.mock.calls[0][0] as number;
    expect(z).toBeGreaterThan(1);
    expect(z).toBeLessThanOrEqual(4);
  });

  it("受控縮放：紙張尺寸跟著 zoom", () => {
    const { paper, rerender } = setup({ zoom: 1, onZoomChange: vi.fn() });
    const w1 = parseFloat(paper.style.width);
    rerender(
      <ToastProvider>
        <LineFixCanvas {...baseProps({ zoom: 2, onZoomChange: vi.fn() })} />
      </ToastProvider>,
    );
    expect(parseFloat(paper.style.width)).toBeCloseTo(w1 * 2, 3);
  });
});

describe("ZoomControls", () => {
  it("顯示百分比，按鈕走刻度", () => {
    const onZoomChange = vi.fn();
    render(<ZoomControls zoom={1} onZoomChange={onZoomChange} />);
    expect(screen.getByText("100%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(onZoomChange).toHaveBeenLastCalledWith(1.5);
    fireEvent.click(screen.getByRole("button", { name: "縮小" }));
    expect(onZoomChange).toHaveBeenLastCalledWith(0.75);
  });
});
