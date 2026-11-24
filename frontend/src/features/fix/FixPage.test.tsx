import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { DEFAULT_PLACEMENT, type BookOverview, type Generation, type Page, type Placement } from "@/api/client";
import { movePlacement } from "@/lib/placement";
import { FIX_POLL_MS } from "./fixApi";
import { FixPage } from "./FixPage";
import type { FixFilter } from "@/lib/fixHref";
import { mkPage, ovPage } from "./testData";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/books/1/page/5",
}));

// 修線畫布與合成舞台換成可操作的替身（兩者另有測試）
vi.mock("@/features/editing/LineFixCanvas", async (orig) => {
  const real = await orig<typeof import("@/features/editing/LineFixCanvas")>();
  return {
    ...real,
    LineFixCanvas: (p: { strokes: unknown[]; onChange: (s: unknown[]) => void; initialTool?: string }) => (
      <div data-testid="linefix" data-tool={p.initialTool}>
        <button type="button" onClick={() => p.onChange([...p.strokes, { op: "draw", width_px: 11, points: [[0.5, 0.5]] }])}>
          stub-draw
        </button>
      </div>
    ),
  };
});
// realStage.on：用真的 ComposeStage（鍵盤測試）
const realStage = vi.hoisted(() => ({ on: false }));
vi.mock("@/features/editing/ComposeStage", async (orig) => {
  const real = await orig<typeof import("@/features/editing/ComposeStage")>();
  return {
    ...real,
    ComposeStage: (p: import("@/features/editing/ComposeStage").ComposeStageProps) =>
      realStage.on ? (
        <real.ComposeStage {...p} />
      ) : (
        <div data-testid="compose" data-bg={p.backgroundUrl} data-cut={p.cutoutUrl} data-cx={p.placement.cx}>
          <button type="button" onClick={() => p.onChange({ ...p.placement, cx: 0.3 })}>
            stub-drag
          </button>
          <button type="button" onClick={() => p.onChange({ ...p.placement, cx: +(p.placement.cx - 0.1).toFixed(2) })}>
            stub-nudge
          </button>
        </div>
      ),
  };
});

const book = {
  id: 1,
  title: "小兔子的交通工具書",
  paper_size: "a4",
  print_mode: "home",
  character_generation_id: 9,
  cover_url: null,
  shared_at: null,
  layout: { content_px: [2244, 3272] },
  stats: { pages: 3, passed: 2 },
};

const gen = (id: number, kind: Generation["kind"], extra: Partial<Generation> = {}) =>
  ({
    id,
    book_id: 1,
    page_id: 5,
    kind,
    status: "succeeded",
    discarded: false,
    user_text: "",
    width: 100,
    height: 140,
    image_url: `/api/generations/${id}/image`,
    cutout_url: kind === "pose" ? `/api/generations/${id}/cutout` : null,
    source: "generated",
    error: null,
    elapsed_s: 1,
    queue_position: null,
    progress: null,
    created_at: "2026-11-12T01:00:00Z",
    ...extra,
  }) as Generation;

let page: Page;
let overview: BookOverview;
let gens: Generation[];
let calls: { url: string; method: string; body?: unknown }[];
let respond: Record<string, () => Response>;

function json(b: unknown, status = 200) {
  return new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
}

beforeEach(() => {
  push.mockReset();
  calls = [];
  respond = {};
  page = mkPage();
  overview = {
    book_id: 1,
    pages: [ovPage(4, 0, "attention"), ovPage(5, 1, "ready"), ovPage(6, 2, "ready"), ovPage(7, 3, "attention")],
    progress: { queued: 0, running: 0, done: 4 },
    progress_pages: { ready: 2, total: 4 },
    estimate_s: 0,
    deferred_count: 0,
    redraw_count: 0,
    run_started_at: "2026-11-12T01:00:00Z",
  };
  gens = [gen(10, "background"), gen(20, "pose"), gen(30, "composite")];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ url, method, body });
      const key = `${method} ${url}`;
      if (respond[key]) return respond[key]();
      if (url === "/api/books/1") return json(book);
      if (url === "/api/books/1/overview") return json(overview);
      if (url === "/api/books/1/pages") return json([page]);
      if (url.startsWith("/api/books/1/generations")) return json(gens);
      if (url === "/api/pages/5/qa")
        return json({ status: "passed", errors: [], warnings: [], gaps: [[0.2, 0.3], [0.6, 0.7]], gap_count: 2,
          character_check: null, qa_note: "", forced_pass_reason: null,
          lineart_url: "/api/pages/5/lineart?v=k1" });
      if (url === "/api/pages/5/lineart/edits" && method === "GET") return json({ generation_id: 30, strokes: [] });
      if (url.startsWith("/api/pages/5/lineart/edits") && method === "PUT")
        return json({ status: "passed", errors: [], warnings: [], gaps: [], gap_count: 0, character_check: null, qa_note: "", forced_pass_reason: null,
          lineart_url: "/api/pages/5/lineart?v=k2" });
      if (method === "POST" && /\/api\/pages\/5\/(undo|redo|restore|accept)$/.test(url)) return json(page);
      if (method === "POST" && url.startsWith("/api/pages/5/poses")) return json(gen(21, "pose", { status: "queued" }), 202);
      if (method === "POST" && url.startsWith("/api/pages/5/backgrounds")) return json(gen(11, "background", { status: "queued" }), 202);
      if (method === "PUT" && url.startsWith("/api/pages/5/placement")) {
        page = { ...page, placement: { autofit: false, ...(body as Placement) } };
        return json(page);
      }
      if (method === "PATCH" && url.startsWith("/api/pages/5")) return json(page);
      if (method === "POST" && url === "/api/books/1/produce") return json({ queued: [], deferred_pages: [], pipeline_pages: [5] }, 202);
      if (url === "/api/books/1/produce/plan") return json({ backgrounds: 0, poses: 0, total: 0, estimate_s: 0, remaining: 5, fits: 0 });
      return json({ queued: 0, running: 0, comfy_ok: true });
    }),
  );
});
afterEach(() => {
  realStage.on = false;
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

function setup(filter: FixFilter = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <FixPage bookId={1} pageId={5} filter={filter} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const posted = (re: RegExp, method = "POST") => calls.filter((c) => c.method === method && re.test(c.url));
const row = (name: string) => screen.getByRole("listitem", { name });
const findRow = (name: string) => screen.findByRole("listitem", { name });

describe("C 處理一頁：頁首", () => {
  it("shows the back link, title, status, 進階編輯 and the preview", async () => {
    setup();
    expect(await screen.findByRole("heading", { level: 1, name: "第 2 頁・消防車" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "整本書" })).toHaveAttribute("href", "/books/1");
    expect(await screen.findByText("可以印 ✓")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "進階編輯" })).toHaveAttribute("href", "/books/1/editor?page=5");
    expect(screen.getByRole("img", { name: "要印的線稿" })).toHaveAttribute("src", expect.stringMatching(/^\/api\/pages\/5\/print\.png\?v=/));
  });

  it("復原／重做 follow can_undo / can_redo and call the endpoints", async () => {
    page = mkPage({ can_undo: true, can_redo: false });
    const user = userEvent.setup();
    setup();
    const undo = await screen.findByRole("button", { name: "復原" });
    const redo = screen.getByRole("button", { name: "重做" });
    expect(undo).toBeEnabled();
    expect(redo).toBeDisabled();
    page = mkPage({ can_undo: false, can_redo: true });
    await user.click(undo);
    await waitFor(() => expect(posted(/\/api\/pages\/5\/undo$/)).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "重做" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "復原" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重做" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/redo$/)).toHaveLength(1));
  });

  it("a redrawn page (review_pose) shows 請看一下 in the header but stays printable", async () => {
    page = mkPage({ status: "passed", attention: "review_pose", attention_detail: "請看一下：換了新的主角動作" });
    overview = { ...overview, pages: overview.pages.map((p) => (p.id === 5 ? { ...p, state: "ready", review: true } : p)) };
    setup();
    await screen.findByRole("heading", { level: 1 });
    expect(await screen.findByText("請看一下", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("可以印 ✓", { exact: true })).toBeNull();
    expect(screen.getByRole("button", { name: "這頁可以印了 ✓" })).toBeEnabled();
  });

  it("a redrawn page (review_pose) that is working again shows 製作中 in the header", async () => {
    page = mkPage({ status: "passed", attention: "review_pose", attention_detail: "請看一下：換了新的主角動作" });
    overview = { ...overview, pages: overview.pages.map((p) => (p.id === 5 ? { ...p, state: "working", review: true } : p)) };
    setup();
    const head = (await screen.findByRole("heading", { level: 1 })).closest("header")!;
    await waitFor(() => expect(within(head).getByText("製作中", { exact: true })).toBeInTheDocument());
    expect(within(head).queryByText("請看一下", { exact: true })).toBeNull();
  });

  it("a redrawn page (review_pose) that is working again hides the review note", async () => {
    page = mkPage({ status: "passed", attention: "review_pose", attention_detail: "請看一下：換了新的主角動作" });
    overview = { ...overview, pages: overview.pages.map((p) => (p.id === 5 ? { ...p, state: "working", review: true } : p)) };
    setup();
    const head = (await screen.findByRole("heading", { level: 1 })).closest("header")!;
    await waitFor(() => expect(within(head).getByText("製作中", { exact: true })).toBeInTheDocument());
    expect(screen.queryByText("請看一下：換了新的主角動作")).toBeNull();
  });

  it("a portrait page whose background had no frame (review_frame) shows 請看一下 in the header but stays printable", async () => {
    page = mkPage({ status: "passed", attention: "review_frame", attention_detail: "這張背景找不到框，照一般擺放，可以再畫一張背景" });
    overview = { ...overview, pages: overview.pages.map((p) => (p.id === 5 ? { ...p, state: "ready", review: true } : p)) };
    setup();
    const head = (await screen.findByRole("heading", { level: 1 })).closest("header")!;
    await waitFor(() => expect(within(head).getByText("請看一下", { exact: true })).toBeInTheDocument());
    expect(within(head).queryByText("可以印 ✓", { exact: true })).toBeNull();
    expect(screen.getByRole("button", { name: "這頁可以印了 ✓" })).toBeEnabled();
  });

  it("prev/next go through all pages without a filter", async () => {
    setup();
    const nav = await screen.findByRole("navigation", { name: "換頁" });
    await waitFor(() => expect(within(nav).getByRole("link", { name: "上一頁" })).toHaveAttribute("href", "/books/1/page/4"));
    expect(within(nav).getByRole("link", { name: "下一頁" })).toHaveAttribute("href", "/books/1/page/6");
  });

  it("prev/next go through attention pages only with filter=attention", async () => {
    setup("attention");
    const nav = await screen.findByRole("navigation", { name: "換頁" });
    await waitFor(() =>
      expect(within(nav).getByRole("link", { name: "上一頁" })).toHaveAttribute("href", "/books/1/page/4?filter=attention"),
    );
    expect(within(nav).getByRole("link", { name: "下一頁" })).toHaveAttribute("href", "/books/1/page/7?filter=attention");
  });
});

describe("C 處理一頁：還原成自動畫的那張", () => {
  it("shows restore only on items that differ from the auto choice, and calls restore", async () => {
    page = mkPage({ background_generation_id: 11, has_user_strokes: true });
    gens = [...gens, gen(11, "background", { source: "upload" })];
    const user = userEvent.setup();
    setup();
    await screen.findByRole("heading", { level: 1 });
    const restoreName = /還原成自動畫的那張/;
    expect(within(row("背景")).getByRole("button", { name: restoreName })).toBeInTheDocument();
    expect(within(row("角色動作")).queryByRole("button", { name: restoreName })).toBeNull();
    expect(within(row("圖下方的字")).queryByRole("button", { name: restoreName })).toBeNull();
    await user.click(within(row("背景")).getByRole("button", { name: restoreName }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/restore$/)).toHaveLength(1));
    expect(posted(/\/restore$/)[0].body).toEqual({ item: "background" });
    await user.click(within(row("線條")).getByRole("button", { name: restoreName }));
    await waitFor(() => expect(posted(/\/restore$/)).toHaveLength(2));
    expect(posted(/\/restore$/)[1].body).toEqual({ item: "lines" });
  });

  it("no restore buttons on a fully automatic page", async () => {
    setup();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: /還原成自動畫的那張/ })).toBeNull();
  });
});

describe("C 處理一頁：畫筆", () => {
  it("pen and eraser draw with oneclick", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(within(await findRow("線條")).getByRole("button", { name: "畫筆" }));
    const canvas = await screen.findByTestId("linefix");
    expect(canvas).toHaveAttribute("data-tool", "draw");
    await user.click(within(canvas).getByRole("button", { name: "stub-draw" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/lineart\/edits\?oneclick=true$/, "PUT")).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "畫好了" }));
    expect(screen.queryByTestId("linefix")).toBeNull();
    await user.click(within(row("線條")).getByRole("button", { name: "擦除" }));
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-tool", "erase");
  });

  it("pen disabled while recompose is pending, with the reason", async () => {
    page = mkPage({ recompose_pending: true });
    setup();
    const r = await screen.findByRole("listitem", { name: "線條" });
    expect(within(r).getByRole("button", { name: "畫筆" })).toBeDisabled();
    expect(within(r).getByRole("button", { name: "擦除" })).toBeDisabled();
    expect(within(r).getByText("這一頁正在重新合成，請稍候再畫")).toBeInTheDocument();
    expect(within(r).getByRole("button", { name: "自動補線" })).toBeDisabled();
  });

  it("pen disabled after a failed recompose says to retry first", async () => {
    page = mkPage({ recompose_pending: true, attention: "failed", attention_detail: "圖片檔案遺失，請重新產生" });
    setup();
    const r = await screen.findByRole("listitem", { name: "線條" });
    expect(within(r).getByRole("button", { name: "畫筆" })).toBeDisabled();
    expect(within(r).getByText("這一頁合成失敗，請先按「再試一次」")).toBeInTheDocument();
  });
});

describe("C 處理一頁：請看一下", () => {
  it("reason card only for failed pages; 再試一次 retries only this page", async () => {
    page = mkPage({ attention: "failed", attention_detail: "圖片產生失敗，請重新產生" });
    respond["POST /api/pages/5/retry"] = () => json({ ...page, requeued: ["pose"], pipeline: false });
    const user = userEvent.setup();
    setup();
    const card = await screen.findByRole("region", { name: "為什麼請你看" });
    expect(card).toHaveTextContent("圖片產生失敗，請重新產生");
    expect(card).toHaveTextContent("按「再試一次」重新做這一頁；也可以重畫主角、AI 再畫一張背景或上傳背景。");
    await user.click(within(card).getByRole("button", { name: "再試一次" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/retry$/)).toHaveLength(1));
    expect(posted(/\/produce$/)).toHaveLength(0);
    expect(await screen.findByText("重新畫這一頁的角色動作了，好了會自動換上去")).toBeInTheDocument();
  });

  it("再試一次 that re-runs the page says so; 429 shows the cap message", async () => {
    page = mkPage({ attention: "failed", attention_detail: "自動處理這一頁時發生錯誤，請再試一次" });
    respond["POST /api/pages/5/retry"] = () => json({ ...page, requeued: [], pipeline: true });
    const user = userEvent.setup();
    setup();
    const card = await screen.findByRole("region", { name: "為什麼請你看" });
    await user.click(within(card).getByRole("button", { name: "再試一次" }));
    expect(await screen.findByText("重新做這一頁了，好了會自動更新")).toBeInTheDocument();
    respond["POST /api/pages/5/retry"] = () => json({ detail: "今天的生成次數已用完，明天再來" }, 429);
    await user.click(within(card).getByRole("button", { name: "再試一次" }));
    expect(await screen.findByText("今天的生成次數已用完，明天再來")).toBeInTheDocument();
  });

  it("review_pose shows the detail note and still offers 這頁可以印了 ✓", async () => {
    page = mkPage({ attention: "review_pose", attention_detail: "請看一下：換了新的主角動作" });
    const user = userEvent.setup();
    setup();
    expect(await screen.findByText("請看一下：換了新的主角動作")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "為什麼請你看" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "這頁可以印了 ✓" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/accept$/)).toHaveLength(1));
  });

  it("review_frame shows the detail note (no reason card) and still offers 這頁可以印了 ✓", async () => {
    page = mkPage({ attention: "review_frame", attention_detail: "這張背景找不到框，照一般擺放，可以再畫一張背景" });
    setup();
    expect(await screen.findByText("這張背景找不到框，照一般擺放，可以再畫一張背景")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "為什麼請你看" })).toBeNull();
    expect(screen.getByRole("button", { name: "這頁可以印了 ✓" })).toBeEnabled();
  });

  it("no reason card when the page is fine", async () => {
    setup();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("region", { name: "為什麼請你看" })).toBeNull();
  });
});

describe("C 處理一頁：這頁可以印了", () => {
  it("accepts and moves on to the next page", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole("button", { name: "這頁可以印了 ✓" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/accept$/)).toHaveLength(1));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1/page/6"));
  });

  it("with filter=attention moves on to the next attention page", async () => {
    const user = userEvent.setup();
    setup("attention");
    await screen.findByRole("navigation", { name: "換頁" });
    await waitFor(() => expect(screen.getByRole("link", { name: "下一頁" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "這頁可以印了 ✓" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1/page/7?filter=attention"));
  });

  it("goes back to the book at the end", async () => {
    overview = { ...overview, pages: [ovPage(4, 0, "ready"), ovPage(5, 1, "ready")] };
    const user = userEvent.setup();
    setup();
    await waitFor(() => expect(screen.getByRole("link", { name: "上一頁" })).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "這頁可以印了 ✓" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
  });

  it("disabled with the reason while the page is being re-composed", async () => {
    page = mkPage({ recompose_pending: true });
    setup();
    const btn = await screen.findByRole("button", { name: "這頁可以印了 ✓" });
    expect(btn).toBeDisabled();
    expect(screen.getByText("這一頁還在重新合成，等它做完再按")).toBeInTheDocument();
  });

  it("after a failed re-compose says to retry first", async () => {
    page = mkPage({ recompose_pending: true, attention: "failed", attention_detail: "圖片檔案遺失，請重新產生" });
    setup();
    expect(await screen.findByRole("button", { name: "這頁可以印了 ✓" })).toBeDisabled();
    expect(screen.getByText("這一頁合成失敗，請先按「再試一次」再印")).toBeInTheDocument();
  });

  it("not offered without a composite", async () => {
    page = mkPage({ selected_generation_id: null, lineart_url: null });
    setup();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: "這頁可以印了 ✓" })).toBeNull();
  });
});

describe("C 處理一頁：重畫主角／AI 再畫一張（新圖好了就換上去）", () => {
  it("角色動作「重畫主角」 and 背景「AI 再畫一張」 use oneclick so the new image is taken", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(within(await findRow("角色動作")).getByRole("button", { name: "重畫主角" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/poses/)).toHaveLength(1));
    expect(posted(/\/poses/)[0].url).toBe("/api/pages/5/poses?oneclick=true");
    expect(posted(/\/poses/)[0].body).toEqual({ action: "開著消防車揮手", count: 1 });
    await user.click(within(row("背景")).getByRole("button", { name: "AI 再畫一張" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/backgrounds/)).toHaveLength(1));
    expect(posted(/\/backgrounds/)[0].url).toBe("/api/pages/5/backgrounds?oneclick=true");
  });

  it("the 表情 and 面向 chips are always visible; a chip saves the action with oneclick", async () => {
    const user = userEvent.setup();
    setup();
    await findRow("角色動作");
    expect(within(row("角色動作")).queryByRole("button", { name: "改動作" })).toBeNull();
    expect(within(row("角色動作")).queryByRole("button", { name: "換一張" })).toBeNull();
    const expr = screen.getByRole("group", { name: "表情" });
    expect(screen.getByRole("group", { name: "面向" })).toBeInTheDocument();
    await user.click(within(expr).getByRole("button", { name: "開心" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(1));
    expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")[0].body).toEqual({
      action_prompt: "開著消防車揮手, with a happy smiling expression",
    });
  });
});

describe("C 處理一頁：畫過的角色動作與重畫主角（#4B）", () => {
  /** 這頁畫過的角色動作：20（使用中）、21 失敗、22、23、24 已捨棄、26 沒有圖；背景與合成圖。 */
  function withPoses() {
    gens = [
      gen(10, "background"),
      gen(20, "pose"),
      gen(21, "pose", { status: "failed", image_url: null, cutout_url: null, error: "boom" }),
      gen(22, "pose"),
      gen(23, "pose"),
      gen(24, "pose", { discarded: true }),
      gen(26, "pose", { image_url: null, cutout_url: null }),
      gen(30, "composite"),
    ];
  }
  const history = () => screen.findByRole("list", { name: "畫過的角色動作" });

  it("lists every pose newest first, current tagged 使用中", async () => {
    withPoses();
    setup();
    const list = await history();
    const items = within(list).getAllByRole("button");
    expect(items.map((b) => b.querySelector("img")?.getAttribute("src"))).toEqual([
      "/api/generations/23/cutout",
      "/api/generations/22/cutout",
      "/api/generations/20/cutout",
    ]);
    expect(items[2]).toHaveAttribute("aria-pressed", "true");
    expect(items[2]).toHaveTextContent("使用中");
    expect(items[0]).toHaveAttribute("aria-pressed", "false");
    expect(items[0]).not.toHaveTextContent("使用中");
    expect(within(row("角色動作")).getByText("這頁畫過 3 張・點一下就換")).toBeInTheDocument();
    // 第一次打開這頁：比使用中（20）新的標「新」
    expect(within(list).getAllByText("新")).toHaveLength(2);
    expect(items[2]).not.toHaveTextContent("新");
    await waitFor(() => expect(localStorage.getItem("cb:seenPose:5")).toBe("23"));
  });

  it("first visit after a batch redraw: the candidate newer than the taken pose is 新; the next visit has none", async () => {
    // 批次重畫：22 換上去（使用中），23 是候選；這頁從沒在 C 打開過
    withPoses();
    page = mkPage({ pose_generation_id: 22 });
    const { unmount } = setup();
    let list = await history();
    let items = within(list).getAllByRole("button");
    expect(items[0]).toHaveTextContent("新"); // 23 候選
    expect(items[1]).toHaveTextContent("使用中"); // 22
    expect(items[1]).not.toHaveTextContent("新");
    expect(items[2]).not.toHaveTextContent("新"); // 20 舊的
    await waitFor(() => expect(localStorage.getItem("cb:seenPose:5")).toBe("23"));
    unmount();
    // 第二次打開：看過了，沒有「新」
    setup();
    list = await history();
    items = within(list).getAllByRole("button");
    expect(items).toHaveLength(3);
    expect(within(list).queryByText("新")).toBeNull();
  });

  it("poses newer than the last visit are tagged 新", async () => {
    withPoses();
    localStorage.setItem("cb:seenPose:5", "20");
    setup();
    const list = await history();
    const items = within(list).getAllByRole("button");
    expect(items[0]).toHaveTextContent("新");
    expect(items[1]).toHaveTextContent("新");
    expect(items[2]).not.toHaveTextContent("新");
    // 看過了：記下最新的一張，畫面上的「新」留到下次打開
    await waitFor(() => expect(localStorage.getItem("cb:seenPose:5")).toBe("23"));
    expect(within(list).getAllByText("新")).toHaveLength(2);
  });

  it("clicking a pose posts /pose?oneclick=true with its id", async () => {
    withPoses();
    respond["POST /api/pages/5/pose?oneclick=true"] = () => {
      page = { ...page, pose_generation_id: (calls[calls.length - 1].body as { generation_id: number }).generation_id, can_undo: true };
      return json(page);
    };
    const user = userEvent.setup();
    setup();
    const list = await history();
    // 使用中的那張點了不會送
    await user.click(within(list).getAllByRole("button")[2]);
    expect(posted(/\/api\/pages\/5\/pose\?/)).toHaveLength(0);
    await user.click(within(list).getAllByRole("button")[0]);
    await waitFor(() => expect(posted(/\/api\/pages\/5\/pose\?/)).toHaveLength(1));
    expect(posted(/\/pose\?/)[0]).toMatchObject({ url: "/api/pages/5/pose?oneclick=true", body: { generation_id: 23 } });
    await waitFor(() => expect(within(list).getAllByRole("button")[0]).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByRole("button", { name: "復原" })).toBeEnabled();
  });

  it("a pick waits for the action save, and 復原 is disabled until it is done", async () => {
    withPoses();
    page = mkPage({ can_undo: true });
    let releaseSave: () => void = () => {};
    const saveGate = new Promise<void>((r) => (releaseSave = r));
    let releasePick: () => void = () => {};
    const pickGate = new Promise<void>((r) => (releasePick = r));
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/pages/5?oneclick=true" && init?.method === "PATCH") await saveGate;
      if (url === "/api/pages/5/pose?oneclick=true" && init?.method === "POST") {
        await realFetch(url, init); // 記下請求
        await pickGate;
        page = { ...page, pose_generation_id: 23 };
        return json(page);
      }
      return realFetch(url, init);
    });
    const user = userEvent.setup();
    setup();
    const list = await history();
    const box = screen.getByRole("textbox", { name: "動作描述" });
    fireEvent.change(box, { target: { value: "開著消防車去郵局" } });
    fireEvent.blur(box);
    await user.click(within(list).getAllByRole("button")[0]);
    // 動作還在存：先不換
    await new Promise((r) => setTimeout(r, 20));
    expect(posted(/\/api\/pages\/5\/pose\?/)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "復原" })).toBeDisabled();
    releaseSave();
    await waitFor(() => expect(posted(/\/api\/pages\/5\/pose\?/)).toHaveLength(1));
    // 換的請求還在路上：復原停用
    expect(screen.getByRole("button", { name: "復原" })).toBeDisabled();
    releasePick();
    await waitFor(() => expect(screen.getByRole("button", { name: "復原" })).toBeEnabled());
    await waitFor(() => expect(within(list).getAllByRole("button")[0]).toHaveAttribute("aria-pressed", "true"));
  });

  it("重畫主角 with 一次畫 2 張 posts count 2 after saving the action", async () => {
    const user = userEvent.setup();
    setup();
    await findRow("角色動作");
    const counts = screen.getByRole("radiogroup", { name: "一次畫幾張" });
    expect(within(counts).getByRole("radio", { name: "1" })).toHaveAttribute("aria-checked", "true");
    fireEvent.change(screen.getByRole("textbox", { name: "動作描述" }), { target: { value: "開著消防車去郵局" } });
    await user.click(within(counts).getByRole("radio", { name: "2" }));
    await user.click(within(row("角色動作")).getByRole("button", { name: "重畫主角" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/poses/)).toHaveLength(1));
    expect(posted(/\/poses/)[0]).toMatchObject({
      url: "/api/pages/5/poses?oneclick=true",
      body: { action: "開著消防車去郵局", count: 2 },
    });
    const patch = calls.findIndex((c) => c.method === "PATCH" && c.url === "/api/pages/5?oneclick=true");
    const pose = calls.findIndex((c) => c.method === "POST" && c.url.startsWith("/api/pages/5/poses"));
    expect(patch).toBeGreaterThanOrEqual(0);
    expect(calls[patch].body).toEqual({ action_prompt: "開著消防車去郵局" });
    expect(patch).toBeLessThan(pose);
  });

  it("重畫主角 is disabled while a pose is being drawn, and the row says so", async () => {
    gens = [...gens, gen(25, "pose", { status: "running" })];
    setup();
    await findRow("角色動作");
    expect(await within(row("角色動作")).findByText("正在畫新的一張…")).toBeInTheDocument();
    expect(within(row("角色動作")).getByRole("button", { name: "重畫主角" })).toBeDisabled();
  });

  it("renders without 新 badges when localStorage throws", async () => {
    withPoses();
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    setup();
    const list = await history();
    expect(within(list).getAllByRole("button")).toHaveLength(3);
    expect(within(list).queryByText("新")).toBeNull();
    expect(within(list).getAllByRole("button")[2]).toHaveTextContent("使用中");
  });
});

describe("C 處理一頁：預覽", () => {
  it("gap checkbox circles the unclosed lines", async () => {
    const user = userEvent.setup();
    setup();
    const box = await screen.findByRole("checkbox", { name: "圈出沒接起來的線（2 處）" });
    await user.click(box);
    expect(screen.getAllByTestId("gap-marker")).toHaveLength(2);
    expect(screen.getByRole("img", { name: "線稿，橘色圈標出沒接起來的線" })).toHaveAttribute("src", "/api/pages/5/lineart?v=k1");
  });

  it("dragging the character on the colour view saves the placement with oneclick", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    const stage = await screen.findByTestId("compose");
    expect(stage).toHaveAttribute("data-bg", "/api/generations/10/image");
    expect(stage).toHaveAttribute("data-cut", "/api/generations/20/cutout");
    await user.click(within(stage).getByRole("button", { name: "stub-drag" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/placement\?oneclick=true$/, "PUT")).toHaveLength(1));
    expect(posted(/\/placement/, "PUT")[0].body).toMatchObject({ cx: 0.3 });
    expect(screen.getByTestId("compose")).toHaveAttribute("data-cx", "0.3");
  });

  it("復原 waits while a placement save is in flight, then runs after it", async () => {
    page = mkPage({ can_undo: true });
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const order: string[] = [];
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/pages/5/placement")) {
        await gate;
        order.push("placement");
      }
      if (url === "/api/pages/5/undo") order.push("undo");
      return realFetch(url, init);
    });
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    await user.click(within(await screen.findByTestId("compose")).getByRole("button", { name: "stub-drag" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "復原" })).toBeDisabled());
    release();
    await waitFor(() => expect(order).toEqual(["placement"]));
    await waitFor(() => expect(screen.getByRole("button", { name: "復原" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(order).toEqual(["placement", "undo"]));
  });
});

describe("C 處理一頁：圖下方的字（原本在故事頁的規則）", () => {
  const patches = () => posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH").map((c) => c.body);
  async function openCaptions() {
    setup();
    await userEvent.click(within(await findRow("圖下方的字")).getByRole("button", { name: "改字" }));
    return { zh: screen.getByRole("textbox", { name: "中文字" }), en: screen.getByRole("textbox", { name: "英文字" }) };
  }

  it("離開欄位就存，存的是正規化後的值（換行、零寬字元）", async () => {
    const { zh, en } = await openCaptions();
    expect(zh).toHaveValue("消防車");
    fireEvent.change(zh, { target: { value: "消 ​防" } });
    fireEvent.blur(zh);
    await waitFor(() => expect(patches()).toEqual([{ caption_zh: "消 防" }]));
    fireEvent.change(en, { target: { value: "​\n" } });
    fireEvent.blur(en);
    await waitFor(() => expect(patches()).toEqual([{ caption_zh: "消 防" }, { caption_en: "" }]));
  });

  it("清空會存空字串", async () => {
    const { zh } = await openCaptions();
    await userEvent.clear(zh);
    await userEvent.tab();
    await waitFor(() => expect(patches()).toEqual([{ caption_zh: "" }]));
  });

  it("超過上限：顯示訊息、不存；𠮷 以 code point 計，12 個剛好可以", async () => {
    const { zh, en } = await openCaptions();
    fireEvent.change(zh, { target: { value: "𠮷".repeat(12) } });
    fireEvent.blur(zh);
    await waitFor(() => expect(patches()).toEqual([{ caption_zh: "𠮷".repeat(12) }]));
    expect(screen.queryByText("中文字最多 12 個字")).not.toBeInTheDocument();

    fireEvent.change(zh, { target: { value: "𠮷".repeat(13) } });
    fireEvent.blur(zh);
    expect(screen.getByText("中文字最多 12 個字")).toBeInTheDocument();
    fireEvent.change(en, { target: { value: "x".repeat(29) } });
    fireEvent.blur(en);
    expect(screen.getByText("英文字最多 28 個字元")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 20));
    expect(patches()).toHaveLength(1);
  });
});

describe("C 處理一頁：圖下方的字要印哪幾行（中文／英文）", () => {
  const patches = () => posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH").map((c) => c.body);
  /** PATCH 套用到 page（伺服器存了開關）。 */
  function serverApplies() {
    respond["PATCH /api/pages/5?oneclick=true"] = () => {
      page = { ...page, ...(calls[calls.length - 1].body as Partial<Page>), can_undo: true };
      return json(page);
    };
  }
  const chips = async () => {
    const group = within(await findRow("圖下方的字")).getByRole("group", { name: "要印哪幾行" });
    return {
      zh: within(group).getByRole("button", { name: /中文/ }),
      en: within(group).getByRole("button", { name: /英文/ }),
    };
  };

  it("two toggle chips, both on by default, with the hint; no need to open 改字", async () => {
    setup();
    const { zh, en } = await chips();
    expect(zh).toHaveAttribute("aria-pressed", "true");
    expect(en).toHaveAttribute("aria-pressed", "true");
    expect(within(row("圖下方的字")).getByText("兩個都不選＝圖下方留白，讓小朋友自己寫")).toBeInTheDocument();
    expect(within(row("圖下方的字")).getByRole("button", { name: "把這個選擇套用到整本書" })).toBeInTheDocument();
  });

  it("turning 英文 off saves with oneclick, the chip and summary follow, and the preview reloads", async () => {
    serverApplies();
    const user = userEvent.setup();
    setup();
    const before = (await screen.findByRole("img", { name: "要印的線稿" })).getAttribute("src");
    await user.click((await chips()).en);
    await waitFor(() => expect(patches()).toEqual([{ caption_show_en: false }]));
    await waitFor(() => expect(screen.getByRole("button", { name: /英文/ })).toHaveAttribute("aria-pressed", "false"));
    expect(screen.getByRole("button", { name: /中文/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(row("圖下方的字")).getByText("消防車")).toBeInTheDocument();
    const after = screen.getByRole("img", { name: "要印的線稿" }).getAttribute("src");
    expect(after).not.toBe(before);
    expect(after).toMatch(/^\/api\/pages\/5\/print\.png\?v=/);
  });

  it("both off: summary says the space under the art is left blank", async () => {
    serverApplies();
    const user = userEvent.setup();
    setup();
    await user.click((await chips()).zh);
    await waitFor(() => expect(screen.getByRole("button", { name: /中文/ })).toHaveAttribute("aria-pressed", "false"));
    await user.click(screen.getByRole("button", { name: /英文/ }));
    await waitFor(() => expect(patches()).toEqual([{ caption_show_zh: false }, { caption_show_en: false }]));
    expect(await within(row("圖下方的字")).findByText("不印字・圖下方留白")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /中文/ }));
    await waitFor(() => expect(patches()).toHaveLength(3));
    expect(patches()[2]).toEqual({ caption_show_zh: true });
  });

  it("把這個選擇套用到整本書 posts this page's toggles and toasts", async () => {
    page = mkPage({ caption_show_zh: false });
    respond["POST /api/books/1/caption-display"] = () => json([page, mkPage({ id: 6, position: 2, caption_show_zh: false })]);
    const user = userEvent.setup();
    setup();
    await user.click(within(await findRow("圖下方的字")).getByRole("button", { name: "把這個選擇套用到整本書" }));
    await waitFor(() => expect(posted(/^\/api\/books\/1\/caption-display$/)).toHaveLength(1));
    expect(posted(/caption-display/)[0].body).toEqual({ show_zh: false, show_en: true });
    expect(await screen.findByText("整本書都改成「只印英文」了")).toBeInTheDocument();
  });

  it("套用到整本書 failing shows an error toast", async () => {
    respond["POST /api/books/1/caption-display"] = () => json({ detail: "找不到這本繪本" }, 404);
    const user = userEvent.setup();
    setup();
    await user.click(within(await findRow("圖下方的字")).getByRole("button", { name: "把這個選擇套用到整本書" }));
    expect(await screen.findByText("找不到這本繪本")).toBeInTheDocument();
  });

  it("toggles and 套用 are off while the page is being re-composed (aria-disabled, clicks ignored)", async () => {
    page = mkPage({ recompose_pending: true });
    const user = userEvent.setup();
    setup();
    const { zh, en } = await chips();
    const apply = within(row("圖下方的字")).getByRole("button", { name: "把這個選擇套用到整本書" });
    for (const b of [zh, en, apply]) expect(b).toHaveAttribute("aria-disabled", "true");
    await user.click(en);
    await user.click(apply);
    expect(patches()).toEqual([]);
    expect(posted(/caption-display/)).toEqual([]);
  });

  it("a chip keeps keyboard focus while its save is in flight, and a second press is ignored", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    serverApplies();
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/pages/5?oneclick=true") && init?.method === "PATCH") await gate;
      return realFetch(url, init);
    });
    const user = userEvent.setup();
    setup();
    const { en } = await chips();
    en.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("button", { name: /英文/ })).toHaveAttribute("aria-disabled", "true"));
    const saving = screen.getByRole("button", { name: /英文/ });
    expect(saving).not.toBeDisabled();
    expect(saving).toHaveFocus();
    await user.keyboard("{Enter}");
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /英文/ })).toHaveAttribute("aria-pressed", "false"));
    expect(screen.getByRole("button", { name: /英文/ })).toHaveFocus();
    expect(screen.getByRole("button", { name: /英文/ })).not.toHaveAttribute("aria-disabled", "true");
    expect(patches()).toEqual([{ caption_show_en: false }]);
  });

  it("toggles wait while a placement save is in flight", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/pages/5/placement")) await gate;
      return realFetch(url, init);
    });
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    await user.click(within(await screen.findByTestId("compose")).getByRole("button", { name: "stub-drag" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /英文/ })).toHaveAttribute("aria-disabled", "true"));
    release();
    await waitFor(() => expect(screen.getByRole("button", { name: /英文/ })).not.toHaveAttribute("aria-disabled", "true"));
  });
});

describe("C 處理一頁：改場景", () => {
  it("改場景：離開欄位就存（oneclick PATCH scene_prompt）", async () => {
    setup();
    const bg = await findRow("背景");
    expect(bg).toHaveTextContent("場景：消防局前面");
    await userEvent.click(within(bg).getByRole("button", { name: "改場景" }));
    const box = screen.getByRole("textbox", { name: "場景描述" });
    expect(box).toHaveValue("消防局前面");
    await userEvent.clear(box);
    await userEvent.type(box, "下雪的消防局");
    await userEvent.tab();
    await waitFor(() => expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(1));
    expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")[0].body).toEqual({ scene_prompt: "下雪的消防局" });
  });

  it("改好場景直接按 AI 再畫一張：先存場景，再排新的背景（用新的場景）", async () => {
    setup();
    const bg = await findRow("背景");
    await userEvent.click(within(bg).getByRole("button", { name: "改場景" }));
    fireEvent.change(screen.getByRole("textbox", { name: "場景描述" }), { target: { value: "夜晚的消防局" } });
    fireEvent.click(within(bg).getByRole("button", { name: "AI 再畫一張" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/backgrounds/)).toHaveLength(1));
    const iPatch = calls.findIndex((c) => c.method === "PATCH" && c.url === "/api/pages/5?oneclick=true");
    const iBg = calls.findIndex((c) => c.method === "POST" && c.url.startsWith("/api/pages/5/backgrounds"));
    expect(iPatch).toBeGreaterThanOrEqual(0);
    expect(calls[iPatch].body).toEqual({ scene_prompt: "夜晚的消防局" });
    expect(iPatch).toBeLessThan(iBg);
  });

  it("場景存不了：不排新的背景", async () => {
    respond["PATCH /api/pages/5?oneclick=true"] = () => json({ detail: "場景太長" }, 422);
    setup();
    const bg = await findRow("背景");
    await userEvent.click(within(bg).getByRole("button", { name: "改場景" }));
    fireEvent.change(screen.getByRole("textbox", { name: "場景描述" }), { target: { value: "夜晚" } });
    fireEvent.click(within(bg).getByRole("button", { name: "AI 再畫一張" }));
    expect(await screen.findByText("場景太長")).toBeInTheDocument();
    expect(posted(/\/api\/pages\/5\/backgrounds/)).toHaveLength(0);
  });
});

describe("C 處理一頁：復原／重做／還原之後文字欄位跟著伺服器", () => {
  /** PATCH 套用到 page（伺服器存了文字）；undo 把 page 換回 before。 */
  function serverWithUndo(before: Partial<Page>) {
    page = mkPage({ can_undo: true });
    respond["PATCH /api/pages/5?oneclick=true"] = () => {
      page = { ...page, ...(calls[calls.length - 1].body as Partial<Page>), can_undo: true };
      return json(page);
    };
    respond["POST /api/pages/5/undo"] = () => {
      page = { ...page, ...before, can_redo: true };
      return json(page);
    };
  }

  it("改動作 → 復原：欄位顯示復原後的動作，重畫主角用它畫", async () => {
    serverWithUndo({ action_prompt: "開著消防車揮手" });
    const user = userEvent.setup();
    setup();
    await findRow("角色動作");
    const box = screen.getByRole("textbox", { name: "動作描述" });
    fireEvent.change(box, { target: { value: "開著消防車去郵局" } });
    fireEvent.blur(box);
    await waitFor(() => expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "復原" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/undo$/)).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "動作描述" })).toHaveValue("開著消防車揮手"));

    await user.click(within(row("角色動作")).getByRole("button", { name: "重畫主角" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/poses/)).toHaveLength(1));
    expect(posted(/\/poses/)[0].body).toEqual({ action: "開著消防車揮手", count: 1 });
    // 復原的文字沒有被重新存回去
    expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(1);
  });

  it("復原之後再打一次同樣的文字，會重新存", async () => {
    serverWithUndo({ action_prompt: "開著消防車揮手" });
    const user = userEvent.setup();
    setup();
    await findRow("角色動作");
    const box = screen.getByRole("textbox", { name: "動作描述" });
    fireEvent.change(box, { target: { value: "開著消防車去郵局" } });
    fireEvent.blur(box);
    await waitFor(() => expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "動作描述" })).toHaveValue("開著消防車揮手"));
    const again = screen.getByRole("textbox", { name: "動作描述" });
    fireEvent.change(again, { target: { value: "開著消防車去郵局" } });
    fireEvent.blur(again);
    await waitFor(() => expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(2));
  });

  it("改字 → 復原：中文字欄位顯示復原後的字", async () => {
    serverWithUndo({ caption_zh: "消防車" });
    const user = userEvent.setup();
    setup();
    await user.click(within(await findRow("圖下方的字")).getByRole("button", { name: "改字" }));
    const zh = screen.getByRole("textbox", { name: "中文字" });
    fireEvent.change(zh, { target: { value: "郵局" } });
    fireEvent.blur(zh);
    await waitFor(() => expect(posted(/\/api\/pages\/5\?oneclick=true$/, "PATCH")).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(posted(/\/api\/pages\/5\/undo$/)).toHaveLength(1));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "中文字" })).toHaveValue("消防車"));
  });
});

describe("C 處理一頁：製作中的提示", () => {
  it("live region 一直都在：沒在做時是空的，在做時才放進文字", async () => {
    setup();
    await screen.findByRole("heading", { level: 1, name: "第 2 頁・消防車" });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("重新合成中：同一個 status 裡寫出正在重新做", async () => {
    page = mkPage({ recompose_pending: true });
    setup();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("正在重新做這一頁…好了會自動更新"));
  });
});

describe("C 處理一頁：失敗的重新合成", () => {
  it("failed recompose (pending + attention failed): no polling, no 正在重新做 text", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    page = mkPage({ recompose_pending: true, attention: "failed", attention_detail: "合成失敗" });
    overview.pages[1] = ovPage(5, 1, "attention");
    setup();
    await screen.findByRole("region", { name: "為什麼請你看" });
    const pageGets = () => calls.filter((c) => c.method === "GET" && c.url === "/api/books/1/pages").length;
    expect(pageGets()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FIX_POLL_MS + 500);
    });
    expect(pageGets()).toBe(1);
    expect(screen.queryByText(/正在重新做這一頁/)).toBeNull();
  });
});

describe("C 處理一頁：位置存檔（合併、鍵盤 debounce）", () => {
  const puts = () => posted(/\/api\/pages\/5\/placement\?oneclick=true$/, "PUT");
  function setSize(img: HTMLElement, w: number, h: number) {
    Object.defineProperty(img, "naturalWidth", { configurable: true, value: w });
    Object.defineProperty(img, "naturalHeight", { configurable: true, value: h });
    fireEvent.load(img);
  }

  it("holding arrow key yields one placement PUT after release", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    realStage.on = true;
    setup();
    fireEvent.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    setSize(await screen.findByRole("img", { name: "背景圖" }), 1000, 800);
    setSize(screen.getByTestId("cutout"), 200, 400);
    const box = screen.getByRole("group", { name: /角色位置/ });
    let expected = DEFAULT_PLACEMENT;
    // 按住 → 鍵約一秒（鍵盤自動重複約 30 次）
    for (let i = 0; i < 30; i++) {
      fireEvent.keyDown(box, { key: "ArrowRight" });
      expected = movePlacement(expected, 0.01, 0);
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(puts()).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual(expected);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(puts()).toHaveLength(1);
  });

  it("放進框裡: frame-fit → one oneclick PUT with the fit; the server's written-back placement shows and is not re-sent", async () => {
    realStage.on = true;
    const fit = { cx: 0.5, cy: 0.62, height: 1.4, flip: false, frame: { x: 0.5, y: 0.55 }, autofit: true };
    const written = { ...fit, cy: 0.64, height: 1.45 };
    respond["POST /api/pages/5/frame-fit"] = () => json(fit);
    respond["PUT /api/pages/5/placement?oneclick=true"] = () => {
      const res = json({ ...page, placement: fit });
      page = { ...page, placement: written }; // 頁面流程合成時寫回依框對齊的位置
      return res;
    };
    setup();
    fireEvent.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    setSize(await screen.findByRole("img", { name: "背景圖" }), 1000, 800);
    setSize(screen.getByTestId("cutout"), 200, 400);
    fireEvent.click(screen.getByRole("button", { name: "放進框裡（肖像）" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(posted(/\/api\/pages\/5\/frame-fit$/)).toHaveLength(1);
    expect(puts()[0].body).toEqual(fit);
    const box = () => screen.getByRole("group", { name: /角色位置/ });
    // 寫回的位置：高 1.45 → 145%；上緣 0.64 − 0.725 → −8.5%
    await waitFor(() => expect(box().style.height).toBe("145%"));
    expect(box().style.top).toBe("-8.5%");
    expect(screen.getByRole("button", { name: "放進框裡（肖像）" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("frame-mask-probe")).toHaveAttribute("src", "/api/generations/10/frame-mask?x=0.5&y=0.55");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 600));
    });
    expect(puts()).toHaveLength(1);
  });

  it("rapid drags send at most one in-flight PUT and the last value wins", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    let inFlight = 0;
    let maxInFlight = 0;
    const sent: number[] = [];
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (!url.startsWith("/api/pages/5/placement")) return realFetch(url, init);
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      sent.push(JSON.parse(String(init?.body)).cx);
      await gate;
      try {
        return await realFetch(url, init);
      } finally {
        inFlight -= 1;
      }
    });
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    const stage = await screen.findByTestId("compose");
    await user.click(within(stage).getByRole("button", { name: "stub-drag" })); // cx 0.3，送出中
    await waitFor(() => expect(sent).toEqual([0.3]));
    for (let i = 0; i < 3; i++) await user.click(within(stage).getByRole("button", { name: "stub-nudge" })); // 0.2 → 0.1 → 0
    expect(screen.getByTestId("compose")).toHaveAttribute("data-cx", "0");
    expect(sent).toEqual([0.3]);
    release();
    await waitFor(() => expect(sent).toEqual([0.3, 0]));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(maxInFlight).toBe(1);
    expect(page.placement).toMatchObject({ cx: 0 });
  });

  it("beforeunload set while placement in flight", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    let sent = false;
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/pages/5/placement")) {
        sent = true;
        await gate;
      }
      return realFetch(url, init);
    });
    const unload = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    const user = userEvent.setup();
    setup();
    await user.click(await screen.findByRole("radio", { name: "彩色原圖" }));
    expect(unload()).toBe(false);
    await user.click(within(await screen.findByTestId("compose")).getByRole("button", { name: "stub-drag" }));
    await waitFor(() => expect(sent).toBe(true));
    expect(unload()).toBe(true);
    release();
    await waitFor(() => expect(puts()).toHaveLength(1));
    await waitFor(() => expect(unload()).toBe(false));
  });
});

describe("C 處理一頁：文字存檔與復原的順序", () => {
  it("undo waits for a pending caption save", async () => {
    page = mkPage({ can_undo: true });
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const log: string[] = [];
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/pages/5?oneclick=true" && init?.method === "PATCH") {
        log.push("PATCH sent");
        await gate;
        const res = await realFetch(url, init);
        log.push("PATCH done");
        return res;
      }
      if (url === "/api/pages/5/undo") log.push("undo sent");
      return realFetch(url, init);
    });
    const user = userEvent.setup();
    setup();
    await user.click(within(await findRow("圖下方的字")).getByRole("button", { name: "改字" }));
    const zh = screen.getByRole("textbox", { name: "中文字" });
    fireEvent.change(zh, { target: { value: "郵局" } });
    const undoBtn = screen.getByRole("button", { name: "復原" });
    // 離開欄位（blur 送出 PATCH）與按下「復原」在同一輪：按鈕還沒來得及停用
    act(() => {
      fireEvent.blur(zh);
      fireEvent.click(undoBtn);
    });
    await waitFor(() => expect(log).toEqual(["PATCH sent"]));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(log).toEqual(["PATCH sent"]);
    release();
    await waitFor(() => expect(log).toEqual(["PATCH sent", "PATCH done", "undo sent"]));
  });
});

describe("C 處理一頁：改完文字直接按（按下按鈕時欄位 blur，存檔還在路上）", () => {
  function gatePatch() {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const log: string[] = [];
    const realFetch = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/pages/5?oneclick=true" && init?.method === "PATCH") {
        log.push("PATCH sent");
        await gate;
        const res = await realFetch(url, init);
        log.push("PATCH done");
        return res;
      }
      if (url === "/api/pages/5/undo") log.push("undo sent");
      if (url === "/api/pages/5/accept") log.push("accept sent");
      return realFetch(url, init);
    });
    return { log, release: () => release() };
  }
  async function typeCaption(user: ReturnType<typeof userEvent.setup>) {
    await user.click(within(await findRow("圖下方的字")).getByRole("button", { name: "改字" }));
    const zh = screen.getByRole("textbox", { name: "中文字" });
    await user.clear(zh);
    await user.type(zh, "郵局");
    expect(zh).toHaveFocus();
  }

  it("one click on 復原 right after typing: undo is sent after the caption PATCH", async () => {
    page = mkPage({ can_undo: true });
    const { log, release } = gatePatch();
    const user = userEvent.setup();
    setup();
    await typeCaption(user);
    // 按下（mousedown 讓欄位 blur → PATCH 送出、還在路上）再放開：一下就要算數
    await user.click(screen.getByRole("button", { name: "復原" }));
    await waitFor(() => expect(log).toEqual(["PATCH sent"]));
    release();
    await waitFor(() => expect(log).toEqual(["PATCH sent", "PATCH done", "undo sent"]));
  });

  it("one click on 這頁可以印了 right after typing: accept is sent after the caption PATCH", async () => {
    const { log, release } = gatePatch();
    const user = userEvent.setup();
    setup();
    await typeCaption(user);
    await user.click(screen.getByRole("button", { name: "這頁可以印了 ✓" }));
    await waitFor(() => expect(log).toEqual(["PATCH sent"]));
    release();
    await waitFor(() => expect(log).toEqual(["PATCH sent", "PATCH done", "accept sent"]));
  });
});

describe("C 處理一頁：線稿 QA 載入失敗", () => {
  it("QA error shows message and retry", async () => {
    respond["GET /api/pages/5/qa"] = () => json({ detail: "圖片檔案遺失" }, 404);
    const user = userEvent.setup();
    setup();
    const lines = await findRow("線條");
    expect(await within(lines).findByText("圖片檔案遺失")).toBeInTheDocument();
    expect(within(lines).getByRole("button", { name: "再試一次" })).toBeInTheDocument();
    await user.click(within(lines).getByRole("button", { name: "畫筆" }));
    const preview = screen.getByRole("region", { name: "預覽" });
    expect(within(preview).getByText("圖片檔案遺失")).toBeInTheDocument();
    expect(within(lines).getByRole("alert")).toHaveTextContent("圖片檔案遺失"); // 只在線條列宣讀一次
    expect(within(preview).queryByRole("alert")).toBeNull();
    expect(within(preview).queryByText("載入線稿中…")).toBeNull();
    delete respond["GET /api/pages/5/qa"];
    await user.click(within(preview).getByRole("button", { name: "再試一次" }));
    expect(await screen.findByTestId("linefix")).toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/api/pages/5/qa")).toHaveLength(2);
  });
});
