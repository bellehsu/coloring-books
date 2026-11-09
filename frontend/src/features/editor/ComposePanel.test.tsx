import { useState, useSyncExternalStore } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_PLACEMENT, type Page, type Placement } from "@/api/client";
import { useCompose } from "@/api/hooks";
import { ComposePanel, type ComposePanelProps } from "./ComposePanel";
import { ComposeStage } from "./ComposeStage";
import { placementSaverFor } from "./placementSaver";

const page = { id: 1, book_id: 1 } as unknown as Page;
const P: Placement = { cx: 0.42, cy: 0.7, height: 0.35, flip: false };

// ---------- 假後端：記錄呼叫順序；可擋住某個請求、或讓它失敗 ----------
let calls: { method: string; url: string; body?: unknown }[];
let gate: Record<string, Promise<void> | undefined>;
let failWith: Record<string, number>;
const j = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  calls = [];
  gate = {};
  failWith = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, url, body });
      const g = gate[`${method} ${url}`];
      if (g) await g;
      if (failWith[`${method} ${url}`]) return j({ detail: "伺服器壞了" }, failWith[`${method} ${url}`]);
      return j({ id: 1, book_id: 1, placement: body ?? null, selected_generation_id: 50 });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

const idx = (method: string, url: string) => calls.findIndex((c) => c.method === method && c.url === url);
const count = (method: string, url: string) => calls.filter((c) => c.method === method && c.url === url).length;

function panel(o: Partial<ComposePanelProps> = {}, qc = new QueryClient()) {
  const props: ComposePanelProps = {
    page,
    placement: P,
    onPlacementChange: vi.fn(),
    onConfirm: vi.fn(async () => {}),
    confirming: false,
    ...o,
  };
  const r = render(
    <QueryClientProvider client={qc}>
      <ComposePanel {...props} />
    </QueryClientProvider>,
  );
  return { props, qc, r };
}

describe("ComposePanel", () => {
  it("水平翻轉：切換 flip（aria-pressed 反映目前狀態）", async () => {
    const { props } = panel();
    const flip = screen.getByRole("button", { name: "水平翻轉" });
    expect(flip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(flip);
    expect(props.onPlacementChange).toHaveBeenCalledWith({ ...P, flip: true });
  });

  it("重設位置：回到預設位置與大小，保留翻轉", async () => {
    const { props } = panel({ placement: { ...P, flip: true } });
    await userEvent.click(screen.getByRole("button", { name: "重設位置" }));
    expect(props.onPlacementChange).toHaveBeenCalledWith({ ...DEFAULT_PLACEMENT, flip: true });
  });

  it("顯示目前位置與大小", () => {
    panel();
    expect(screen.getByText("水平 42%・垂直 70%・高度 35%")).toBeInTheDocument();
  });

  it("確認合成中：按鈕全部停用，顯示合成中", () => {
    panel({ confirming: true });
    expect(screen.getByRole("button", { name: "合成中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "水平翻轉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重設位置" })).toBeDisabled();
  });

  it("連點確認合成只呼叫一次（父層還沒重畫前的第二下也擋住）", async () => {
    let finish!: () => void;
    const onConfirm = vi.fn(() => new Promise<void>((r) => (finish = r)));
    panel({ onConfirm });
    const btn = screen.getByRole("button", { name: "確認合成" });
    fireEvent.click(btn);
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await act(async () => finish());
    fireEvent.click(btn);
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });

  it("位置寫入失敗：持續顯示「位置尚未儲存」與重試；重試重送最後的位置", async () => {
    const qc = new QueryClient();
    const saver = placementSaverFor(qc);
    failWith["PUT /api/pages/1/placement"] = 500;
    panel({}, qc);
    act(() => saver.savePlacement(1, P));
    const status = await screen.findByTestId("placement-save-failed");
    expect(status).toHaveTextContent("位置尚未儲存");
    delete failWith["PUT /api/pages/1/placement"];
    await userEvent.click(screen.getByRole("button", { name: "重試" }));
    await waitFor(() => expect(count("PUT", "/api/pages/1/placement")).toBe(2));
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([P, P]);
    await waitFor(() => expect(screen.queryByTestId("placement-save-failed")).toBeNull());
  });
});

// ---------- 舞台＋面板＋placementSaver＋useCompose 接起來（與 EditorPage 相同的接法） ----------
function Harness() {
  const qc = useQueryClient();
  const saver = placementSaverFor(qc);
  const latest = useSyncExternalStore(saver.subscribe, () => saver.latest(1), () => undefined);
  const placement = latest ?? DEFAULT_PLACEMENT;
  const compose = useCompose(1);
  const [confirming, setConfirming] = useState(false);
  const onConfirm = async () => {
    setConfirming(true);
    try {
      await compose.mutateAsync({ pageId: 1 });
    } catch {
      /* EditorPage 會顯示 toast */
    } finally {
      setConfirming(false);
    }
  };
  return (
    <>
      <ComposeStage
        backgroundUrl="/bg.png"
        cutoutUrl="/cutout.png"
        placement={placement}
        onChange={(p) => saver.savePlacement(1, p)}
        disabled={confirming}
      />
      <ComposePanel
        page={page}
        placement={placement}
        onPlacementChange={(p) => saver.savePlacement(1, p)}
        onConfirm={onConfirm}
        confirming={confirming}
      />
    </>
  );
}

function flow() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness />
    </QueryClientProvider>,
  );
  const set = (el: HTMLElement, w: number, h: number) => {
    Object.defineProperty(el, "naturalWidth", { configurable: true, value: w });
    Object.defineProperty(el, "naturalHeight", { configurable: true, value: h });
    fireEvent.load(el);
  };
  set(screen.getByRole("img", { name: "背景圖" }), 1000, 800);
  set(screen.getByTestId("cutout"), 200, 400);
  screen.getByTestId("compose-overlay").getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 500, height: 400, right: 500, bottom: 400, x: 0, y: 0, toJSON() {} }) as DOMRect;
  return screen.getByRole("group", { name: /角色位置/ });
}

/** 背景 1000×800、去背角色寬高比 0.5：高 h → 寬 h×800×0.5 px；left% = (cx×1000 − 寬/2) / 10。 */
const leftPct = (p: Placement) => `${+((p.cx * 1000 - (p.height * 800 * 0.5) / 2) / 10).toFixed(4)}%`;

describe("compose step: placement and confirm", () => {
  it("confirm flushes placement before compose：位置 PUT 還在送時按確認 → compose POST 在 PUT 之後，合成用的就是畫面上的位置", async () => {
    const box = flow();
    let release!: () => void;
    gate["PUT /api/pages/1/placement"] = new Promise<void>((r) => (release = r));
    fireEvent.keyDown(box, { key: "ArrowRight", shiftKey: true });
    const onScreen = { ...DEFAULT_PLACEMENT, cx: 0.55 };
    expect(box.style.left).toBe(leftPct(onScreen));
    await waitFor(() => expect(count("PUT", "/api/pages/1/placement")).toBe(1));
    fireEvent.click(screen.getByRole("button", { name: "確認合成" }));
    fireEvent.click(screen.getByRole("button", { name: /合成/ })); // 連點
    await act(async () => {});
    expect(count("POST", "/api/pages/1/compose")).toBe(0);
    await act(async () => release());
    await waitFor(() => expect(count("POST", "/api/pages/1/compose")).toBe(1));
    expect(idx("PUT", "/api/pages/1/placement")).toBeLessThan(idx("POST", "/api/pages/1/compose"));
    expect(calls.filter((c) => c.method === "PUT").at(-1)!.body).toEqual(onScreen);
    expect(box.style.left).toBe(leftPct(onScreen));
    await waitFor(() => expect(screen.getByRole("button", { name: "確認合成" })).toBeEnabled());
    expect(count("POST", "/api/pages/1/compose")).toBe(1);
  });

  it("一邊拖一邊按確認合成 → 拖曳取消、畫面回到已存的位置，合成結果等於畫面", async () => {
    const box = flow();
    // 先存一個位置
    fireEvent.keyDown(box, { key: "ArrowDown" });
    const saved = { ...DEFAULT_PLACEMENT, cy: 0.67 };
    await waitFor(() => expect(count("PUT", "/api/pages/1/placement")).toBe(1));
    // 拖到一半（沒放開）
    fireEvent.pointerDown(box, { pointerId: 1, button: 0, clientX: 250, clientY: 260 });
    fireEvent.pointerMove(box, { pointerId: 1, clientX: 350, clientY: 260 });
    expect(box.style.left).toBe(leftPct({ ...saved, cx: 0.7 }));
    fireEvent.click(screen.getByRole("button", { name: "確認合成" }));
    // 確認期間畫面就是要合成的位置
    expect(box.style.left).toBe(leftPct(saved));
    fireEvent.pointerMove(box, { pointerId: 1, clientX: 400, clientY: 260 });
    fireEvent.pointerUp(box, { pointerId: 1, clientX: 400, clientY: 260 });
    await waitFor(() => expect(count("POST", "/api/pages/1/compose")).toBe(1));
    // 沒有多送拖曳的位置：伺服器上的位置（合成用的）＝ saved ＝ 畫面
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([saved]);
    expect(box.style.left).toBe(leftPct(saved));
  });
});
