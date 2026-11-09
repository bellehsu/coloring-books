import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { MAX_UNDO } from "@/lib/raster";
import { SketchCanvas, type SketchCanvasProps } from "./SketchCanvas";

/**
 * jsdom 沒有 canvas：用一個「像素模型」的假 2D context。
 * - fillRect(白) → 全部像素白；stroke/fill/strokeRect → 用目前顏色塗滿所有像素
 * - getImageData 回傳複本並帶流水號，putImageData 還原
 * 足以驗證：空白判斷、復原快照、存檔回呼收到 null 或 PNG。
 */
const W = 4;
const H = 4;
let pixels: Uint8ClampedArray;
let snapSeq: number;
let puts: number[];
let toBlobCalls: number;

/** 每次塗色都塗滿全部 16 個像素（≥ MAX_INK_PIXELS，黑色才會被判定為「有墨水」）。 */
const paintPixel0 = (color: unknown) => {
  const v = color === "#ffffff" ? 255 : 0;
  for (let i = 0; i < pixels.length; i += 4) pixels[i] = pixels[i + 1] = pixels[i + 2] = v;
};

function makeCtx() {
  const ctx = {
    strokeStyle: "#000000" as unknown,
    fillStyle: "#000000" as unknown,
    lineWidth: 1,
    lineCap: "butt",
    lineJoin: "miter",
    save() {},
    restore() {},
    beginPath() {},
    closePath() {},
    moveTo() {},
    lineTo() {},
    arc() {},
    ellipse() {},
    stroke() {
      paintPixel0(ctx.strokeStyle);
    },
    fill() {
      paintPixel0(ctx.fillStyle);
    },
    strokeRect() {
      paintPixel0(ctx.strokeStyle);
    },
    fillRect() {
      if (ctx.fillStyle === "#ffffff") pixels.fill(255);
      else paintPixel0(ctx.fillStyle);
    },
    drawImage() {
      paintPixel0("#000000");
    },
    getImageData() {
      return { data: new Uint8ClampedArray(pixels), width: W, height: H, seq: ++snapSeq };
    },
    putImageData(img: { data: Uint8ClampedArray; seq: number }) {
      pixels = new Uint8ClampedArray(img.data);
      puts.push(img.seq);
    },
  };
  return ctx;
}

class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  width = W;
  height = H;
  private _src = "";
  set src(v: string) {
    this._src = v;
    queueMicrotask(() => (v.includes("broken") ? this.onerror?.() : this.onload?.()));
  }
  get src() {
    return this._src;
  }
}

beforeEach(() => {
  pixels = new Uint8ClampedArray(W * H * 4);
  snapSeq = 0;
  puts = [];
  toBlobCalls = 0;
  const ctx = makeCtx();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => ctx as unknown as CanvasRenderingContext2D,
  );
  HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback, type?: string) {
    toBlobCalls++;
    cb(new Blob(["png"], { type: type ?? "image/png" }));
  };
  HTMLCanvasElement.prototype.toDataURL = () => "data:image/png;base64,cG5n";
  HTMLCanvasElement.prototype.setPointerCapture = () => {};
  HTMLCanvasElement.prototype.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: W * 2, height: H * 2, right: W * 2, bottom: H * 2, x: 0, y: 0 }) as DOMRect;
  vi.stubGlobal("Image", FakeImage);
  URL.createObjectURL = () => "blob:ref";
  URL.revokeObjectURL = () => {};
});

afterEach(() => {
  cleanup(); // 先卸載（會觸發 flush 存檔），再還原 canvas stub
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function setup(props: Partial<SketchCanvasProps> = {}) {
  const onSave = vi.fn();
  const onUploadReference = vi.fn(async () => new Blob(["ref"], { type: "image/png" }));
  const utils = render(
    <ToastProvider>
      <SketchCanvas
        width={W}
        height={H}
        guides={null}
        initialUrl={null}
        onSave={onSave}
        onUploadReference={onUploadReference}
        {...props}
      />
    </ToastProvider>,
  );
  const canvas = utils.container.querySelector("canvas")!;
  return { ...utils, canvas, onSave, onUploadReference };
}

async function ready() {
  // 初始載入（含 Image onload 的 microtask）
  await act(async () => {
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
}

function stroke(canvas: HTMLCanvasElement, from = { x: 1, y: 1 }, to = { x: 5, y: 5 }) {
  fireEvent.pointerDown(canvas, { pointerId: 1, button: 0, clientX: from.x, clientY: from.y });
  fireEvent.pointerMove(canvas, { pointerId: 1, clientX: to.x, clientY: to.y });
  fireEvent.pointerUp(canvas, { pointerId: 1, clientX: to.x, clientY: to.y });
}

const tick = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

describe("SketchCanvas", () => {
  it("工具列依草圖順序，畫板 canvas 解析度＝生成尺寸", async () => {
    const { canvas } = setup({ width: W, height: H });
    await ready();
    const bar = screen.getByRole("toolbar", { name: "畫板工具" });
    const labels = Array.from(bar.querySelectorAll("button")).map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["筆刷", "直線", "方框", "圓", "橡皮擦", "上傳參考圖", "復原", "清除畫板"]);
    expect(screen.getByRole("button", { name: "筆刷" })).toHaveAttribute("aria-pressed", "true");
    expect(canvas.width).toBe(W);
    expect(canvas.height).toBe(H);
  });

  it("空白時顯示「角色位置留白」，第一筆之後消失", async () => {
    vi.useFakeTimers();
    const { canvas } = setup();
    await ready();
    expect(screen.getByText("角色位置留白")).toBeInTheDocument();
    stroke(canvas);
    expect(screen.queryByText("角色位置留白")).toBeNull();
  });

  it("落筆結束 1 秒後存 PNG（不是更早）", async () => {
    vi.useFakeTimers();
    const { canvas, onSave } = setup();
    await ready();
    stroke(canvas);
    tick(900);
    expect(onSave).not.toHaveBeenCalled();
    tick(200);
    expect(onSave).toHaveBeenCalledTimes(1);
    const blob = onSave.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("image/png");
  });

  it("全部清除後存檔 → 收到 null，不會上傳空白 PNG（Review Focus #4）", async () => {
    vi.useFakeTimers();
    const { canvas, onSave } = setup();
    await ready();
    stroke(canvas);
    tick(1100);
    expect(onSave).toHaveBeenLastCalledWith(expect.any(Blob));
    const blobsBefore = toBlobCalls;

    fireEvent.click(screen.getByRole("button", { name: "清除畫板" }));
    tick(1100);
    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(null);
    expect(toBlobCalls).toBe(blobsBefore);
  });

  it("較舊的 toBlob 晚於較新的清除才完成 → 丟掉，最後一次 onSave 仍是 null", async () => {
    vi.useFakeTimers();
    let pending: (() => void) | null = null;
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
      pending = () => cb(new Blob(["png"], { type: "image/png" }));
    };
    const { canvas, onSave } = setup();
    await ready();
    stroke(canvas);
    tick(1100);
    expect(pending).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "清除畫板" }));
    tick(1100);
    expect(onSave).toHaveBeenLastCalledWith(null);
    act(() => pending!());
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenLastCalledWith(null);
  });

  it("toBlob 回傳 null → 經同一個 debounce 重試一次", async () => {
    vi.useFakeTimers();
    let n = 0;
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
      n++;
      cb(n === 1 ? null : new Blob(["png"], { type: "image/png" }));
    };
    const { canvas, onSave } = setup();
    await ready();
    stroke(canvas);
    tick(1100);
    expect(n).toBe(1);
    expect(onSave).not.toHaveBeenCalled();
    tick(1100);
    expect(n).toBe(2);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenLastCalledWith(expect.any(Blob));
  });

  it("toBlob 連續回傳 null → 只重試一次，之後留給卸載 flush", async () => {
    vi.useFakeTimers();
    let n = 0;
    HTMLCanvasElement.prototype.toBlob = function (cb: BlobCallback) {
      n++;
      cb(null);
    };
    const { canvas, onSave, unmount } = setup();
    await ready();
    stroke(canvas);
    tick(5000);
    expect(n).toBe(2);
    expect(onSave).not.toHaveBeenCalled();
    unmount();
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toBeInstanceOf(Blob);
  });

  it("用橡皮擦擦到全白，也存 null", async () => {
    vi.useFakeTimers();
    const { canvas, onSave } = setup();
    await ready();
    stroke(canvas);
    fireEvent.click(screen.getByRole("button", { name: "橡皮擦" }));
    stroke(canvas);
    tick(1100);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenLastCalledWith(null);
  });

  it("清除可以復原", async () => {
    vi.useFakeTimers();
    const { canvas, onSave } = setup();
    await ready();
    stroke(canvas);
    fireEvent.click(screen.getByRole("button", { name: "清除畫板" }));
    fireEvent.click(screen.getByRole("button", { name: "復原" }));
    tick(1100);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenLastCalledWith(expect.any(Blob));
  });

  it(`復原最多 ${MAX_UNDO} 步，超過時最舊的被丟棄`, async () => {
    const { canvas } = setup();
    await ready();
    const undo = screen.getByRole("button", { name: "復原" });
    expect(undo).toBeDisabled();
    // 落筆（pointerdown）時第一個 getImageData 就是這一筆的復原快照
    const snapOf: number[] = [];
    for (let i = 0; i < MAX_UNDO + 1; i++) {
      snapOf.push(snapSeq + 1);
      stroke(canvas);
    }
    for (let i = 0; i < MAX_UNDO; i++) {
      expect(undo).toBeEnabled();
      fireEvent.click(undo);
    }
    expect(undo).toBeDisabled();
    // 最後一次復原還原到第 2 筆之前；第 1 筆之前的快照（最舊）已被丟掉
    expect(puts[puts.length - 1]).toBe(snapOf[1]);
    expect(puts).not.toContain(snapOf[0]);
  });

  it("卸載前有未存的變更 → 同步呼叫 onSave", async () => {
    vi.useFakeTimers();
    const { canvas, onSave, unmount } = setup();
    await ready();
    stroke(canvas);
    expect(onSave).not.toHaveBeenCalled();
    unmount();
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toBeInstanceOf(Blob);
  });

  it("沒有變更就卸載 → 不存檔", async () => {
    const { onSave, unmount } = setup();
    await ready();
    unmount();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("有 initialUrl 時載入成底圖，不觸發存檔、不顯示留白提示", async () => {
    vi.useFakeTimers();
    const { onSave } = setup({ initialUrl: "/api/pages/1/sketch?v=3" });
    await ready();
    expect(pixels[0]).toBe(0);
    expect(screen.queryByText("角色位置留白")).toBeNull();
    tick(2000);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("上傳參考圖 → 呼叫 onUploadReference、畫進畫布、可復原並觸發存檔", async () => {
    vi.useFakeTimers();
    const { container, onSave, onUploadReference } = setup();
    await ready();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "ref.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await ready();
    expect(onUploadReference).toHaveBeenCalledWith(file);
    expect(pixels[0]).toBe(0);
    expect(screen.getByRole("button", { name: "復原" })).toBeEnabled();
    tick(1100);
    expect(onSave).toHaveBeenLastCalledWith(expect.any(Blob));
  });

  it("上傳參考圖失敗 → 顯示後端訊息", async () => {
    const { container } = setup({
      onUploadReference: vi.fn(async () => {
        throw new Error("圖片格式不支援");
      }),
    });
    await ready();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(["x"], "a.png")] } });
    });
    await ready();
    expect(screen.getByRole("alert")).toHaveTextContent("圖片格式不支援");
  });

  it("印刷廠模式顯示裁切線與安全區；家用模式只有說明", async () => {
    const guides = { trim: { x: 0.03, y: 0.03, w: 0.94, h: 0.94 }, safe: { x: 0.08, y: 0.08, w: 0.84, h: 0.84 } };
    const { container, unmount } = setup({ guides });
    await ready();
    expect(screen.getByText("裁切線")).toBeInTheDocument();
    expect(screen.getByText("安全區")).toBeInTheDocument();
    expect(container.querySelector('[data-guide="trim"]')).toHaveStyle({ left: "3%", top: "3%", width: "94%" });
    unmount();

    setup({ guides: null });
    await ready();
    expect(screen.queryByText("裁切線")).toBeNull();
    expect(screen.getByText("只畫背景與道具")).toBeInTheDocument();
  });

  it("方框工具：拖曳中預覽（還原快照再畫），放開才算一步", async () => {
    const { canvas } = setup();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "方框" }));
    fireEvent.pointerDown(canvas, { pointerId: 1, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 4, clientY: 4 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 6, clientY: 6 });
    expect(puts.length).toBe(2); // 每次移動都先還原快照
    fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 6, clientY: 6 });
    expect(screen.getByRole("button", { name: "復原" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "復原" }));
    expect(screen.getByRole("button", { name: "復原" })).toBeDisabled();
    expect(pixels[0]).toBe(255);
  });
});
