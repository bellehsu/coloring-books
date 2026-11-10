import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import type { Page } from "@/api/client";
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

function setup(over: Partial<BackgroundPanelProps> = {}, qc = new QueryClient()) {
  const props: BackgroundPanelProps = {
    page: mkPage(),
    onPatch: vi.fn(() => Promise.resolve()),
    beforeGenerate: vi.fn(() => Promise.resolve()),
    onGenerate: vi.fn(() => Promise.resolve()),
    generating: false,
    activeCount: 0,
    ...over,
  };
  const ui = (p: BackgroundPanelProps) => (
    <QueryClientProvider client={qc}>
      <BackgroundPanel key={p.page.id} {...p} />
    </QueryClientProvider>
  );
  const r = render(ui(props));
  return { props, qc, rerender: (p: BackgroundPanelProps) => r.rerender(ui(p)), unmount: r.unmount };
}

const btn = () => screen.getByRole("button", { name: "產生背景" });

describe("BackgroundPanel", () => {
  it("shows the time hint and the button", () => {
    setup();
    expect(btn()).toBeEnabled();
    expect(screen.getByText("約 10 秒")).toBeInTheDocument();
  });

  it("shows the batch hint", () => {
    setup();
    expect(
      screen.getByText("先寫好每一頁的場景描述，再按頁面清單上方的『產生所有背景』，可以減少等待"),
    ).toBeInTheDocument();
  });

  it("shows the queued count instead of the time hint", () => {
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

  it("registerSave: the registered save awaits pending fields and resends a failed scene", async () => {
    const d = deferred();
    const onPatch = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockImplementationOnce(() => d.promise)
      .mockResolvedValue(undefined);
    let save: (() => Promise<unknown>) | null = null;
    const off = vi.fn();
    const registerSave = vi.fn((fn: () => Promise<unknown>) => {
      save = fn;
      return off;
    });
    const { unmount } = setup({ onPatch, registerSave });
    expect(registerSave).toHaveBeenCalled();
    await expect(save!()).resolves.toBeDefined(); // 沒改過 → 不送出
    expect(onPatch).not.toHaveBeenCalled();
    const box = screen.getByLabelText("場景描述");
    fireEvent.change(box, { target: { value: "garden" } });
    fireEvent.blur(box);
    await waitFor(() => expect(onPatch).toHaveBeenCalledTimes(1)); // 失敗
    fireEvent.change(screen.getByLabelText(/構圖強度/), { target: { value: "0.3" } });
    let done = false;
    const p = save!().then(() => (done = true));
    // 重送場景描述（掛起）＋構圖強度
    expect(onPatch).toHaveBeenNthCalledWith(2, { scene_prompt: "garden" });
    expect(onPatch).toHaveBeenNthCalledWith(3, { controlnet_strength: 0.3 });
    await Promise.resolve();
    expect(done).toBe(false);
    d.resolve(undefined);
    await p;
    expect(done).toBe(true);
    unmount();
    expect(off).toHaveBeenCalled();
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
});
