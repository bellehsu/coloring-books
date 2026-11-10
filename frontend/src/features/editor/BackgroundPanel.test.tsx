import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Page } from "@/api/client";
import { readBgStyle, writeBgStyle, type BgStyle } from "@/lib/bgStyle";
import { BackgroundPanel, type BackgroundPanelProps } from "./BackgroundPanel";

const mkPage = (o: Partial<Page> = {}) =>
  ({ id: 1, scene_prompt: "kitchen", controlnet_strength: 0.4, sketch_url: null, ...o }) as Page;

function deferred<T = unknown>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** 風格由呼叫端持有並記住：這裡用 state＋localStorage 模擬 EditorPage，同時記錄 onStyleChange。 */
function Host(props: BackgroundPanelProps) {
  const [style, setStyle] = useState<BgStyle>(props.style);
  return (
    <BackgroundPanel
      {...props}
      style={style}
      onStyleChange={(s) => {
        props.onStyleChange(s);
        writeBgStyle(s);
        setStyle(s);
      }}
    />
  );
}

function setup(over: Partial<BackgroundPanelProps> = {}, qc = new QueryClient()) {
  const props: BackgroundPanelProps = {
    page: mkPage(),
    onPatch: vi.fn(() => Promise.resolve()),
    beforeGenerate: vi.fn(() => Promise.resolve()),
    onGenerate: vi.fn(() => Promise.resolve()),
    generating: false,
    activeCount: 0,
    style: "simple",
    onStyleChange: vi.fn(),
    simpleUnavailableReason: null,
    ...over,
  };
  const ui = (p: BackgroundPanelProps) => (
    <QueryClientProvider client={qc}>
      <Host key={p.page.id} {...p} />
    </QueryClientProvider>
  );
  const r = render(ui(props));
  return { props, qc, rerender: (p: BackgroundPanelProps) => r.rerender(ui(p)), unmount: r.unmount };
}

const btn = () => screen.getByRole("button", { name: "產生背景" });
const styleGroup = () => screen.getByRole("radiogroup", { name: "背景風格" });
const radio = (name: "簡潔" | "細緻") => within(styleGroup()).getByRole("radio", { name });
const SIMPLE_HINT = "約 20 秒，線條少、適合小小孩";
const DETAILED_HINT = "約 10 秒，細節多，可以照構圖草圖";
const SKETCH_NOTE = "有構圖草圖時用細緻，才會照草圖構圖";
const withSketch = (o: Partial<Page> = {}) => mkPage({ sketch_url: "/api/pages/1/sketch?v=1", controlnet_strength: 0.4, ...o });

beforeEach(() => localStorage.clear());

describe("BackgroundPanel", () => {
  it("shows the button and no fixed time hint", () => {
    setup();
    expect(btn()).toBeEnabled();
    expect(screen.queryByText("約 10 秒")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("shows the queued count", () => {
    setup({ activeCount: 2 });
    expect(screen.getByRole("status")).toHaveTextContent("本頁 2 張排隊或生成中");
  });

  it("PATCHes the scene on blur only when changed", async () => {
    const { props } = setup();
    const box = screen.getByLabelText("場景描述");
    fireEvent.focus(box);
    fireEvent.blur(box);
    expect(props.onPatch).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "garden" } });
    fireEvent.blur(box);
    expect(props.onPatch).toHaveBeenCalledWith({ scene_prompt: "garden" });
    fireEvent.blur(box);
    expect(props.onPatch).toHaveBeenCalledTimes(1);
  });

  it("PATCHes the strength on release", () => {
    const { props } = setup();
    const r = screen.getByLabelText(/構圖強度/);
    fireEvent.change(r, { target: { value: "0.3" } });
    expect(props.onPatch).not.toHaveBeenCalled();
    fireEvent.keyUp(r);
    expect(props.onPatch).toHaveBeenCalledWith({ controlnet_strength: 0.3 });
  });

  it("limits the strength to 0.6", () => {
    setup();
    expect(screen.getByLabelText(/構圖強度/)).toHaveAttribute("max", "0.6");
  });

  it("flushes sketch and fields first, then POSTs once", async () => {
    const order: string[] = [];
    const { props } = setup({
      beforeGenerate: vi.fn(async () => void order.push("sketch")),
      onPatch: vi.fn(async (b) => void order.push(`patch:${Object.keys(b)[0]}`)),
      onGenerate: vi.fn(async () => void order.push("post")),
    });
    fireEvent.change(screen.getByLabelText("場景描述"), { target: { value: "garden" } });
    await userEvent.click(btn());
    await waitFor(() => expect(props.onGenerate).toHaveBeenCalledTimes(1));
    expect(order.at(-1)).toBe("post");
    expect(order).toContain("sketch");
    expect(order).toContain("patch:scene_prompt");
  });

  it("awaits the scene PATCH before generating", async () => {
    const d = deferred();
    const { props } = setup({ onPatch: vi.fn(() => d.promise) });
    fireEvent.change(screen.getByLabelText("場景描述"), { target: { value: "garden" } });
    await userEvent.click(btn());
    expect(props.onGenerate).not.toHaveBeenCalled();
    d.resolve(undefined);
    await waitFor(() => expect(props.onGenerate).toHaveBeenCalledTimes(1));
  });

  it("does not generate when the sketch flush fails", async () => {
    const { props } = setup({ beforeGenerate: vi.fn(() => Promise.reject(new Error("x"))) });
    await userEvent.click(btn());
    await waitFor(() => expect(btn()).toBeEnabled());
    expect(props.onGenerate).not.toHaveBeenCalled();
  });

  it("resends a failed scene PATCH on generate, and does not generate if it fails again", async () => {
    const onPatch = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue(undefined);
    const { props } = setup({ onPatch });
    const box = screen.getByLabelText("場景描述");
    fireEvent.change(box, { target: { value: "garden" } });
    fireEvent.blur(box);
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1));
    await userEvent.click(btn()); // 重送，再失敗 → 不產生
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(btn()).toBeEnabled());
    expect(props.onGenerate).not.toHaveBeenCalled();
    await userEvent.click(btn());
    await waitFor(() => expect(props.onGenerate).toHaveBeenCalledTimes(1));
    expect(onPatch).toHaveBeenCalledTimes(3);
  });

  it("a double click during the save sends only one POST and disables the button", async () => {
    const d = deferred();
    const { props } = setup({ beforeGenerate: vi.fn(() => d.promise) });
    fireEvent.click(btn());
    fireEvent.click(btn());
    expect(btn()).toBeDisabled();
    d.resolve(undefined);
    await waitFor(() => expect(props.onGenerate).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(btn()).toBeEnabled());
    expect(props.beforeGenerate).toHaveBeenCalledTimes(1);
  });

  it("is disabled while the POST is in flight", () => {
    setup({ generating: true });
    expect(btn()).toBeDisabled();
  });

  it("a remount mid-PATCH shows the text that was sent", async () => {
    const d = deferred();
    const qc = new QueryClient();
    const first = setup({ onPatch: vi.fn(() => d.promise) }, qc);
    const box = screen.getByLabelText("場景描述");
    fireEvent.change(box, { target: { value: "garden" } });
    fireEvent.blur(box);
    first.unmount();
    setup({}, qc); // 伺服器的 page 還是舊的 "kitchen"
    expect(screen.getByLabelText("場景描述")).toHaveValue("garden");
  });

  describe("背景風格", () => {
    it("簡潔 is checked by default with its hint", () => {
      setup();
      expect(radio("簡潔")).toHaveAttribute("aria-checked", "true");
      expect(radio("簡潔")).toBeEnabled();
      expect(radio("細緻")).toHaveAttribute("aria-checked", "false");
      expect(screen.getByText(SIMPLE_HINT)).toBeInTheDocument();
      expect(styleGroup()).toHaveAccessibleDescription(SIMPLE_HINT);
      expect(screen.queryByText(SKETCH_NOTE)).not.toBeInTheDocument();
    });

    it("choosing 細緻 swaps the hint and stores detailed", async () => {
      const { props } = setup();
      await userEvent.click(radio("細緻"));
      expect(props.onStyleChange).toHaveBeenCalledWith("detailed");
      expect(radio("細緻")).toHaveAttribute("aria-checked", "true");
      expect(screen.getByText(DETAILED_HINT)).toBeInTheDocument();
      expect(screen.queryByText(SIMPLE_HINT)).not.toBeInTheDocument();
      expect(readBgStyle()).toBe("detailed");
    });

    it("a page with a sketch (strength 0.4): 簡潔 disabled, 細緻 shown, note shown, stored choice unchanged", async () => {
      writeBgStyle("simple");
      const { props } = setup({ page: withSketch() });
      expect(radio("簡潔")).toBeDisabled();
      expect(radio("簡潔")).toHaveAttribute("aria-checked", "false");
      expect(radio("細緻")).toHaveAttribute("aria-checked", "true");
      expect(screen.getByText(SKETCH_NOTE)).toBeInTheDocument();
      expect(screen.getByText(DETAILED_HINT)).toBeInTheDocument();
      expect(styleGroup()).toHaveAccessibleDescription(`${DETAILED_HINT} ${SKETCH_NOTE}`);
      await userEvent.click(radio("簡潔"));
      // 重按已（被迫）選中的細緻也不改寫記憶
      await userEvent.click(radio("細緻"));
      expect(props.onStyleChange).not.toHaveBeenCalled();
      expect(localStorage.getItem("coloring.bgStyle")).toBe("simple");
    });

    it("dragging the strength to 0 frees 簡潔 again (live slider value)", () => {
      setup({ page: withSketch() });
      expect(radio("簡潔")).toBeDisabled();
      fireEvent.change(screen.getByLabelText(/構圖強度/), { target: { value: "0" } });
      expect(radio("簡潔")).toBeEnabled();
      expect(radio("簡潔")).toHaveAttribute("aria-checked", "true");
      expect(screen.queryByText(SKETCH_NOTE)).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText(/構圖強度/), { target: { value: "0.05" } });
      expect(radio("簡潔")).toBeDisabled();
    });

    it("hasSketch overrides page.sketch_url (a sketch saved this session before the pages refetch)", () => {
      setup({ page: mkPage({ sketch_url: null }), hasSketch: true });
      expect(radio("簡潔")).toBeDisabled();
      expect(radio("細緻")).toHaveAttribute("aria-checked", "true");
    });

    it("simple unavailable: 簡潔 disabled and the missing list shown", async () => {
      const reason = "簡潔風格缺少：ComfyUI-GGUF、qwen-image-edit-2511-Q4_K_M.gguf";
      const { props } = setup({ simpleUnavailableReason: reason });
      expect(radio("簡潔")).toBeDisabled();
      expect(radio("細緻")).toHaveAttribute("aria-checked", "true");
      expect(screen.getByText(reason)).toBeInTheDocument();
      expect(styleGroup()).toHaveAccessibleDescription(`${DETAILED_HINT} ${reason}`);
      await userEvent.click(radio("簡潔"));
      expect(props.onStyleChange).not.toHaveBeenCalled();
    });

    it("產生背景 sends simple by default", async () => {
      const { props } = setup();
      await userEvent.click(btn());
      await waitFor(() => expect(props.onGenerate).toHaveBeenCalledWith("simple"));
    });

    it("產生背景 sends detailed when chosen", async () => {
      const { props } = setup({ style: "detailed" });
      await userEvent.click(btn());
      await waitFor(() => expect(props.onGenerate).toHaveBeenCalledWith("detailed"));
    });

    it("產生背景 sends the user's choice on a sketch page (the backend forces 細緻), detailed when unavailable", async () => {
      // 送出＝使用者選的（只套用可用性）；草圖改用細緻由後端決定，畫面仍顯示細緻
      const a = setup({ page: withSketch() });
      expect(radio("細緻")).toHaveAttribute("aria-checked", "true");
      await userEvent.click(btn());
      await waitFor(() => expect(a.props.onGenerate).toHaveBeenCalledWith("simple"));
      a.unmount();
      const b = setup({ simpleUnavailableReason: "簡潔風格目前無法使用" });
      await userEvent.click(btn());
      await waitFor(() => expect(b.props.onGenerate).toHaveBeenCalledWith("detailed"));
    });

    it("a sketch that lands while saving (before the POST) still sends the user's choice", async () => {
      const d = deferred();
      const qc = new QueryClient();
      const first = setup({ beforeGenerate: vi.fn(() => d.promise) }, qc);
      await userEvent.click(btn());
      // flush 存好草圖：pages 重抓後 page 帶 sketch_url，面板重新 render
      first.rerender({ ...first.props, page: withSketch() });
      d.resolve(undefined);
      await waitFor(() => expect(first.props.onGenerate).toHaveBeenCalledWith("simple"));
    });

    it("simple becoming unavailable while saving (before the POST) makes the POST detailed", async () => {
      const d = deferred();
      const qc = new QueryClient();
      const first = setup({ beforeGenerate: vi.fn(() => d.promise) }, qc);
      await userEvent.click(btn());
      first.rerender({ ...first.props, simpleUnavailableReason: "簡潔風格目前無法使用" });
      d.resolve(undefined);
      await waitFor(() => expect(first.props.onGenerate).toHaveBeenCalledWith("detailed"));
    });
  });
});
