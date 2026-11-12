import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { Thumbs, type ThumbKind } from "./Thumbs";
import { alertToasts } from "@/components/ui/toastTesting";

type O = Record<string, unknown>;
const gen = (id: number, o: O = {}): O => ({
  id,
  book_id: 1,
  page_id: 1,
  kind: "background",
  status: "succeeded",
  discarded: false,
  user_text: "",
  width: 864,
  height: 1216,
  image_url: `/img/${id}.png`,
  cutout_url: null,
  source: "generated",
  error: null,
  elapsed_s: 3,
  queue_position: null,
  progress: null,
  created_at: "",
  ...o,
});

let gens: O[];
let calls: { method: string; url: string; body?: unknown }[];
let failWith: Record<string, number>;

const j = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  gens = [];
  calls = [];
  failWith = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ method, url, body });
      const fail = failWith[`${method} ${url}`];
      if (fail) return j({ detail: "正被頁面使用或作為角色" }, fail);
      if (url.startsWith("/api/books/1/generations")) {
        const q = new URL(url, "http://x").searchParams;
        let list = gens.filter((g) => g.kind === q.get("kind") && g.page_id === Number(q.get("page_id")));
        if (q.get("include_discarded") !== "true") list = list.filter((g) => !g.discarded);
        return j(list);
      }
      let m = url.match(/^\/api\/generations\/(\d+)$/);
      if (m && method === "PATCH") {
        gens = gens.map((g) => (g.id === Number(m![1]) ? { ...g, ...(body as O) } : g));
        return j(gens.find((g) => g.id === Number(m![1])));
      }
      m = url.match(/^\/api\/generations\/(\d+)\/cancel$/);
      if (m) {
        gens = gens.map((g) => (g.id === Number(m![1]) ? { ...g, status: "cancelled" } : g));
        return j(gens.find((g) => g.id === Number(m![1])));
      }
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0 });
      return j({ detail: "not found" }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function setup(p: { kind?: ThumbKind; selectedId?: number | null; onPick?: (g: Generation) => void; picking?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onPick = p.onPick ?? vi.fn();
  const wrap = (node: ReactNode) => (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  );
  const tree = (selectedId: number | null) =>
    wrap(
      <Thumbs bookId={1} kind={p.kind ?? "background"} pageId={1} selectedId={selectedId} onPick={onPick} picking={p.picking} />,
    );
  const r = render(tree(p.selectedId ?? null));
  return { onPick, rerender: (sel: number | null) => r.rerender(tree(sel)) };
}

const radios = () => within(screen.getByRole("radiogroup")).getAllByRole("radio");

describe("Thumbs", () => {
  it("背景：只抓這頁的背景（不含已捨棄）；舊的在前、依序編號；點一張呼叫 onPick；選定的 aria-checked", async () => {
    gens = [gen(3), gen(5), gen(4, { discarded: true }), gen(6, { kind: "pose" }), gen(7, { page_id: 2 })];
    const { onPick, rerender } = setup({ selectedId: 5 });
    const group = await screen.findByRole("radiogroup", { name: "選擇背景" });
    await waitFor(() => expect(within(group).getAllByRole("radio")).toHaveLength(2));
    expect(radios().map((r) => r.getAttribute("aria-label"))).toEqual(["背景第 1 張", "背景第 2 張"]);
    expect(screen.getByRole("radio", { name: "背景第 2 張" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "背景第 1 張" })).toHaveAttribute("aria-checked", "false");
    await userEvent.click(screen.getByRole("radio", { name: "背景第 1 張" }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
    // 點已選的不會再選一次
    await userEvent.click(screen.getByRole("radio", { name: "背景第 2 張" }));
    expect(onPick).toHaveBeenCalledTimes(1);
    rerender(3);
    expect(screen.getByRole("radio", { name: "背景第 1 張" })).toHaveAttribute("aria-checked", "true");
  });

  it("角色動作：群組名稱與編號跟著 kind；縮圖比例跟著 generation 尺寸", async () => {
    gens = [gen(8, { kind: "pose", width: 832, height: 1216 })];
    setup({ kind: "pose" });
    const r = await screen.findByRole("radio", { name: "角色動作第 1 張" });
    expect(screen.getByRole("radiogroup", { name: "選擇角色動作" })).toBeInTheDocument();
    expect(r.querySelector("[data-aspect]")).toHaveAttribute("data-aspect", "832 / 1216");
  });

  it("排隊／生成中（進度）／失敗／已取消不是選項；排隊中與生成中可取消（POST cancel）", async () => {
    gens = [
      gen(1, { status: "queued", image_url: null, queue_position: 2 }),
      gen(2, { status: "running", image_url: null, queue_position: 0, progress: [3, 8] }),
      gen(3, { status: "failed", image_url: null, error: "ComfyUI 沒有回應" }),
      gen(4, { status: "cancelled", image_url: null }),
    ];
    setup();
    expect(await screen.findByText("排隊第 2 位")).toBeInTheDocument();
    expect(screen.getByText("生成中")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "生成進度" });
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuemax", "8");
    const fail = screen.getByText((_, el) => el?.getAttribute("title") === "ComfyUI 沒有回應");
    // 原因不只在 title：螢幕閱讀器也讀得到
    expect(fail).toHaveTextContent("失敗：ComfyUI 沒有回應");
    expect(within(fail).getByText("：ComfyUI 沒有回應")).toHaveClass(/srOnly/);
    expect(screen.getByText("已取消")).toBeInTheDocument();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "取消背景第 1 張" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/generations/1/cancel")).toBe(true));
    expect(screen.getByRole("button", { name: "取消背景第 2 張" })).toBeInTheDocument();
    // 失敗、已取消的可以捨棄（清掉）
    expect(screen.getByRole("button", { name: "捨棄背景第 3 張" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "捨棄背景第 4 張" })).toBeInTheDocument();
  });

  it("捨棄沒在用的那張：送 PATCH {discarded:true}，從列表消失；使用中的那張沒有捨棄鈕", async () => {
    gens = [gen(3), gen(5)];
    setup({ selectedId: 5 });
    await screen.findByRole("radio", { name: "背景第 2 張" });
    expect(screen.queryByRole("button", { name: "捨棄背景第 2 張" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "捨棄背景第 1 張" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH" && c.url === "/api/generations/3")?.body).toEqual({ discarded: true }),
    );
    await waitFor(() => expect(screen.getAllByRole("radio")).toHaveLength(1));
  });

  it("捨棄被後端拒絕 → toast 顯示原因", async () => {
    gens = [gen(3)];
    failWith["PATCH /api/generations/3"] = 409;
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "捨棄背景第 1 張" }));
    await waitFor(() => expect(alertToasts()).toHaveTextContent("正被頁面使用或作為角色"));
  });

  it("選用進行中：其他張 aria-disabled、點了不選；不用 disabled，鍵盤焦點留在原地", async () => {
    gens = [gen(3), gen(5)];
    const { onPick } = setup({ selectedId: 5, picking: true });
    const other = await screen.findByRole("radio", { name: "背景第 1 張" });
    expect(other).toHaveAttribute("aria-disabled", "true");
    expect(other).not.toBeDisabled();
    expect(screen.getByRole("radio", { name: "背景第 2 張" })).not.toHaveAttribute("aria-disabled");
    other.focus();
    await userEvent.keyboard("{Enter}");
    await userEvent.click(other);
    expect(onPick).not.toHaveBeenCalled();
    expect(other).toHaveFocus();
  });

  it("方向鍵在縮圖間移動焦點（只有一張可 Tab 進入）；Enter 選用", async () => {
    gens = [gen(3), gen(4), gen(5)];
    const { onPick } = setup({ selectedId: 4 });
    await waitFor(() => expect(screen.getAllByRole("radio")).toHaveLength(3));
    expect(radios().map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
    radios()[1].focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(radios()[2]).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(radios()[0]).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}");
    expect(radios()[2]).toHaveFocus();
    expect(onPick).not.toHaveBeenCalled();
    await userEvent.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 5 }));
  });

  it("沒有紀錄時依 kind 顯示提示", async () => {
    setup();
    expect(await screen.findByText("還沒有背景。按「換一張」產生一張，或上傳圖片。")).toBeInTheDocument();
  });

  it("角色動作沒有紀錄時的提示", async () => {
    setup({ kind: "pose" });
    expect(await screen.findByText("還沒有角色動作。寫好動作後按「換一張」。")).toBeInTheDocument();
  });

  it("上傳的背景顯示「上傳」標記，名稱也說明；不再顯示精準／快速／簡潔／細緻", async () => {
    gens = [
      gen(3, { source: "upload" }),
      gen(4),
      gen(6, { kind: "pose" }),
      gen(7, { kind: "pose" }),
    ];
    setup();
    const up = await screen.findByRole("radio", { name: "背景第 1 張（上傳的圖片）" });
    expect(up.closest("[data-tile]")).toHaveTextContent("上傳");
    for (const t of ["簡潔", "細緻", "精準", "快速"]) expect(screen.queryByText(t)).not.toBeInTheDocument();
    cleanup();
    setup({ kind: "pose" });
    await screen.findByRole("radio", { name: "角色動作第 2 張" });
    for (const t of ["精準", "快速", "上傳"]) expect(screen.queryByText(t)).not.toBeInTheDocument();
  });
});
