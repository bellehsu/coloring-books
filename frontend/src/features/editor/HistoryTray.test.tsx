import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { HistoryTray, type HistoryKind } from "./HistoryTray";

type O = Record<string, unknown>;
const gen = (id: number, o: O = {}): O => ({
  id,
  book_id: 1,
  page_id: 1,
  kind: "background",
  status: "succeeded",
  discarded: false,
  prompt: "",
  user_text: "",
  seed: 100 + id,
  width: 864,
  height: 1216,
  image_url: `/img/${id}.png`,
  mask_url: null,
  pose_mode: null,
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
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0, running_id: null });
      return j({ detail: "not found" }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function setup(p: { kind?: HistoryKind; selectedId?: number | null; onPick?: (g: Generation) => void; picking?: boolean } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onPick = p.onPick ?? vi.fn();
  const wrap = (node: ReactNode) => (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  );
  const tree = (selectedId: number | null) =>
    wrap(
      <HistoryTray
        bookId={1}
        kind={p.kind ?? "background"}
        pageId={1}
        selectedId={selectedId}
        onPick={onPick}
        picking={p.picking}
      />,
    );
  const r = render(tree(p.selectedId ?? null));
  return { onPick, rerender: (sel: number | null) => r.rerender(tree(sel)) };
}

describe("HistoryTray", () => {
  it("背景：只抓這頁的背景（含已捨棄）；新的在前；點選呼叫 onPick；選定的顯示「使用中」", async () => {
    gens = [gen(1), gen(2), gen(3), gen(4, { kind: "pose" }), gen(5, { page_id: 2 })];
    const { onPick, rerender } = setup();
    expect(screen.getByRole("region", { name: "背景歷史" })).toBeInTheDocument();
    await screen.findByRole("button", { name: "選 seed 103 為本頁背景" });
    expect(calls.some((c) => c.url === "/api/books/1/generations?kind=background&page_id=1&include_discarded=true")).toBe(
      true,
    );
    const picks = screen.getAllByRole("button", { name: /^選 seed/ });
    expect(picks.map((b) => b.getAttribute("aria-label"))).toEqual([
      "選 seed 103 為本頁背景",
      "選 seed 102 為本頁背景",
      "選 seed 101 為本頁背景",
    ]);
    expect(screen.queryByText("使用中")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "選 seed 102 為本頁背景" }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 2 }));
    rerender(2);
    const btn = screen.getByRole("button", { name: "選 seed 102 為本頁背景" });
    expect(btn).toHaveAttribute("aria-pressed", "true");
    expect(within(btn).getByText("使用中")).toBeInTheDocument();
    // 已在使用中的再點不會重送
    await userEvent.click(btn);
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it("角色動作：標題、按鈕文字與縮圖比例跟著 kind", async () => {
    gens = [gen(7, { kind: "pose", width: 832, height: 1216 }), gen(8)];
    setup({ kind: "pose" });
    expect(screen.getByRole("region", { name: "角色動作歷史" })).toBeInTheDocument();
    const pick = await screen.findByRole("button", { name: "選 seed 107 為本頁角色動作" });
    expect(screen.queryByRole("button", { name: /seed 108/ })).not.toBeInTheDocument();
    expect(pick.querySelector("[data-aspect]")).toHaveAttribute("data-aspect", "832 / 1216");
  });

  it("排隊／生成中／失敗／已取消；排隊中可取消（POST cancel）", async () => {
    gens = [
      gen(1, { status: "failed", image_url: null, error: "ComfyUI 無法連線" }),
      gen(2, { status: "cancelled", image_url: null }),
      gen(3, { status: "running", image_url: null, queue_position: 0, progress: [5, 20] }),
      gen(4, { status: "queued", image_url: null, queue_position: 2 }),
    ];
    setup();
    expect(await screen.findByText("ComfyUI 無法連線")).toBeInTheDocument();
    expect(screen.getByText("已取消")).toBeInTheDocument();
    expect(screen.getByText("生成中")).toBeInTheDocument();
    expect(screen.getByText("排隊第 2 位")).toBeInTheDocument();
    const bar = screen.getByRole("progressbar", { name: "生成進度" });
    expect(bar).toHaveAttribute("aria-valuenow", "5");
    expect(bar).toHaveAttribute("aria-valuemax", "20");
    expect(screen.queryByRole("button", { name: /^選 seed/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "取消 seed 104" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST" && c.url === "/api/generations/4/cancel")).toHaveLength(1));
  });

  it("捨棄的預設隱藏；捨棄送 PATCH；「歷史 N」切換顯示，可還原", async () => {
    gens = [gen(1, { discarded: true }), gen(2)];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "捨棄 seed 102" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH" && c.url === "/api/generations/2")?.body).toEqual({ discarded: true }),
    );
    const hist = await screen.findByRole("button", { name: /歷史 2/ });
    expect(screen.queryByRole("button", { name: "還原 seed 101" })).not.toBeInTheDocument();
    expect(hist).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(hist);
    expect(hist).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "還原 seed 101" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PATCH" && c.url === "/api/generations/1").at(-1)?.body).toEqual({
        discarded: false,
      }),
    );
  });

  it("捨棄被後端拒絕 → toast 顯示原因", async () => {
    gens = [gen(2)];
    failWith["PATCH /api/generations/2"] = 409;
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "捨棄 seed 102" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("正被頁面使用或作為角色");
  });

  it("使用中的那張：捨棄按鈕停用並說明「使用中的不能捨棄」，不送 PATCH", async () => {
    gens = [gen(1), gen(2)];
    setup({ selectedId: 2 });
    const inUse = await screen.findByRole("button", { name: "捨棄 seed 102" });
    expect(inUse).toBeDisabled();
    expect(inUse).toHaveAttribute("title", "使用中的不能捨棄");
    await userEvent.click(inUse);
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    const other = screen.getByRole("button", { name: "捨棄 seed 101" });
    expect(other).not.toBeDisabled();
    expect(other).toHaveAttribute("title", "捨棄這張");
  });

  it("選用進行中：其他張停用", async () => {
    gens = [gen(1), gen(2)];
    setup({ selectedId: 1, picking: true });
    expect(await screen.findByRole("button", { name: "選 seed 102 為本頁背景" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "選 seed 101 為本頁背景" })).not.toBeDisabled();
  });

  it("沒有紀錄時依 kind 顯示提示", async () => {
    setup();
    expect(await screen.findByText("還沒有背景。寫好場景描述後按「產生背景」。")).toBeInTheDocument();
  });

  it("角色動作沒有紀錄時的提示", async () => {
    setup({ kind: "pose" });
    expect(await screen.findByText("還沒有角色動作。輸入動作後按「產生角色動作」。")).toBeInTheDocument();
  });

  it("角色動作：縮圖左上角顯示產生方式「精準」／「快速」（排隊中的也有）", async () => {
    gens = [
      gen(1, { kind: "pose", pose_mode: "fast" }),
      gen(2, { kind: "pose", pose_mode: "precise" }),
      gen(3, { kind: "pose", pose_mode: "precise", status: "queued", image_url: null, queue_position: 1 }),
    ];
    setup({ kind: "pose", selectedId: 2 });
    await screen.findByRole("button", { name: "選 seed 101 為本頁角色動作" });
    const cards = screen.getAllByRole("listitem");
    expect(cards.map((li) => li.querySelector("[data-mode]")?.textContent)).toEqual(["精準", "精準", "快速"]);
    expect(within(cards[1]).getByText("使用中")).toBeInTheDocument();
    expect(within(cards[2]).getByText("快速")).toHaveAttribute("data-mode", "fast");
  });

  it("背景卡片沒有產生方式標記", async () => {
    gens = [gen(1), gen(2, { pose_mode: null })];
    setup({ selectedId: 1 });
    await screen.findByRole("button", { name: "選 seed 102 為本頁背景" });
    expect(screen.queryByText("精準")).not.toBeInTheDocument();
    expect(screen.queryByText("快速")).not.toBeInTheDocument();
    expect(document.querySelector("[data-mode]")).toBeNull();
  });
});
