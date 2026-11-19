import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { editsQueueFor } from "@/features/editing/editsQueue";
import { SetupPage } from "./SetupPage";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: vi.fn() }) }));

type J = Record<string, unknown>;
const bookOf = (o: J = {}): J => ({
  id: 1,
  title: "小兔子的交通工具書",
  paper_size: "a4",
  print_mode: "home",
  character_generation_id: null,
  character_description: null,
  cover_url: null,
  shared_at: null,
  layout: { content_px: [2244, 3272] },
  stats: { pages: 0, passed: 0 },
  ...o,
});
const charGen = (id: number, o: J = {}): J => ({
  id,
  book_id: 1,
  page_id: null,
  kind: "character",
  status: "succeeded",
  discarded: false,
  user_text: "一隻圓圓的小白兔",
  image_url: `/api/generations/${id}/image`,
  source: "generated",
  error: null,
  queue_position: null,
  progress: null,
  ...o,
});
const pageOf = (id: number, position: number, o: J = {}): J => ({
  id,
  book_id: 1,
  position,
  scene_prompt: "",
  action_prompt: "",
  caption_zh: null,
  caption_en: null,
  background_generation_id: null,
  pose_generation_id: null,
  selected_generation_id: null,
  ...o,
});

const STORY = [
  "第1頁\n場景：消防局門口\n動作：開著消防車揮手\n中文：消防車\n英文：Fire Engine",
  "第2頁\n場景：醫院門口\n動作：站在救護車旁邊",
  "第3頁\n場景：公園\n動作：開心地跳起來",
].join("\n\n");

let book: J | null;
let pages: J[];
let gens: J[];
let plan: J;
let calls: { method: string; url: string; body: unknown }[];
let respond: (method: string, url: string, body: unknown) => Response | Promise<Response | undefined> | undefined;
let nextPageId: number;

const json = (b: unknown, status = 200) =>
  new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  push.mockReset();
  book = bookOf();
  pages = [];
  gens = [];
  plan = { backgrounds: 0, poses: 0, total: 0, estimate_s: 0, remaining: 60, fits: 0 };
  calls = [];
  respond = () => undefined;
  nextPageId = 100;
  window.history.replaceState(null, "", "/books/new");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body instanceof FormData ? init.body : init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, url, body });
      const custom = await respond(method, url, body);
      if (custom) return custom;
      if (url === "/api/books" && method === "POST") {
        book = bookOf({ title: (body as J).title, paper_size: (body as J).paper_size, print_mode: (body as J).print_mode });
        return json(book, 201);
      }
      if (url === "/api/books/1" && method === "GET") return json(book);
      if (url === "/api/books/1" && method === "PATCH") return json((book = { ...book, ...(body as J) }));
      if (url === "/api/books/1/pages" && method === "GET") return json(pages);
      if (url === "/api/books/1/pages" && method === "POST") {
        const p = pageOf(++nextPageId, pages.length, body as J);
        pages = [...pages, p];
        return json(p, 201);
      }
      if (url.startsWith("/api/pages/") && method === "PATCH") return json({});
      if (/^\/api\/pages\/\d+\/placement$/.test(url) && method === "PUT") return json({});
      if (url === "/api/books/1/generations?kind=character") return json(gens.filter((g) => g.kind === "character"));
      if (url === "/api/books/1/generations") return json(gens);
      if (url === "/api/books/1/produce/plan") return json(plan);
      if (url === "/api/books/1/character/candidates") {
        const g = charGen(50, { status: "queued", image_url: null });
        gens = [...gens, g];
        return json([g], 202);
      }
      if (url === "/api/books/1/character" && method === "POST") {
        book = { ...book, character_generation_id: (body as J).generation_id };
        return json(book);
      }
      if (url === "/api/books/1/character/upload") {
        gens = [...gens, charGen(77, { source: "upload", user_text: "" })];
        book = { ...book, character_generation_id: 77 };
        return json(book, 201);
      }
      const up = /^\/api\/pages\/(\d+)\/backgrounds\/upload$/.exec(url);
      if (up) {
        // 同後端：上傳圖片成為這頁選用的背景
        const pid = Number(up[1]);
        const gid = 900 + pid;
        gens = [...gens, { ...charGen(gid), kind: "background", page_id: pid, source: "upload", user_text: "" }];
        pages = pages.map((p) => (p.id === pid ? { ...p, background_generation_id: gid } : p));
        return json(pages.find((p) => p.id === pid), 201);
      }
      if (url === "/api/books/1/produce" && method === "POST") return json({ queued: [1], deferred_pages: [], pipeline_pages: [] }, 202);
      return json({ queued: 0, running: 0, comfy_ok: true });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function setup(bookId: number | null = 1) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SetupPage bookId={bookId} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return Object.assign(r, { qc });
}
const posts = (url: string | RegExp) =>
  calls.filter((c) => c.method === "POST" && (typeof url === "string" ? c.url === url : url.test(c.url)));
const startButton = () => screen.getByRole("button", { name: "開始做這本書" });
const paste = (text: string) => fireEvent.change(screen.getByLabelText("把故事貼在這裡"), { target: { value: text } });
const png = (name = "park.png") => new File(["x"], name, { type: "image/png" });

describe("D1：上一頁回到 /books/new 這個元件，但網址是 /books/{id}/setup", () => {
  it("bookId＝null 且 pathname 是 /books/1/setup：載入第 1 本書，不顯示「做一本新的著色書」，書名離開欄位不建新書", async () => {
    book = bookOf({ character_generation_id: 50 });
    pages = [pageOf(101, 0)];
    gens = [charGen(50)];
    window.history.replaceState(null, "", "/books/1/setup");
    setup(null);
    expect(await screen.findByRole("heading", { level: 1, name: "主角與故事" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "做一本新的著色書" })).toBeNull();
    await waitFor(() => expect(screen.getByLabelText("書名")).toHaveValue("小兔子的交通工具書"));
    await userEvent.type(screen.getByLabelText("書名"), "2");
    await userEvent.tab();
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url === "/api/books/1")).toBe(true));
    expect(posts("/api/books")).toHaveLength(0);
  });
});

describe("① 書名和紙張：建立書", () => {
  it("/books/new：書名離開欄位就建立（用選的紙張），網址換成 /books/{id}/setup，不換頁", async () => {
    book = null;
    setup(null);
    expect(screen.getByRole("heading", { level: 1, name: "做一本新的著色書" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /我的書櫃/ })).toHaveAttribute("href", "/");
    await userEvent.selectOptions(screen.getByLabelText("紙張"), "A5・印刷廠");
    await userEvent.type(screen.getByLabelText("書名"), "  小兔子的書 ");
    expect(posts("/api/books")).toHaveLength(0);
    paste(STORY); // 還沒建立書也能貼故事
    await userEvent.tab();
    await waitFor(() => expect(posts("/api/books")).toHaveLength(1));
    expect(posts("/api/books")[0].body).toEqual({ title: "小兔子的書", paper_size: "a5", print_mode: "print_shop" });
    await waitFor(() => expect(window.location.pathname).toBe("/books/1/setup"));
    // 同一個畫面：貼上的故事還在，標題不變
    expect(screen.getByLabelText("把故事貼在這裡")).toHaveValue(STORY);
    expect(screen.getByRole("heading", { level: 1, name: "做一本新的著色書" })).toBeInTheDocument();
    // 再離開一次不會重複建立
    await userEvent.click(screen.getByLabelText("書名"));
    await userEvent.tab();
    expect(posts("/api/books")).toHaveLength(1);
  });

  it("沒寫書名不建立；建立後改書名離開欄位就存，紙張只能換同比例的", async () => {
    book = null;
    setup(null);
    await userEvent.click(screen.getByLabelText("書名"));
    await userEvent.tab();
    expect(posts("/api/books")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "＋ 產生主角" })).toBeDisabled();
    expect(screen.getByText(/先寫書名/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("書名"), "書{enter}");
    await waitFor(() => expect(window.location.pathname).toBe("/books/1/setup"));
    await userEvent.type(screen.getByLabelText("書名"), "二");
    await userEvent.tab();
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH" && c.url === "/api/books/1")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ title: "書二" });
    expect(screen.getByRole("option", { name: "Letter・家用印表機" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "B5・家用印表機" })).toBeEnabled();
  });
});

describe("① 改紙張前等修線存好", () => {
  const stroke = { op: "draw" as const, width_px: 11, points: [[0.5, 0.5]] as [number, number][] };
  const paperPatches = () =>
    calls.filter((c) => c.method === "PATCH" && c.url === "/api/books/1" && "paper_size" in ((c.body as J) ?? {}));

  it("修線還在送：送完才改紙張", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    respond = async (method, url) => {
      if (method === "PUT" && url.includes("/lineart/edits")) {
        await gate;
        return json({ status: "draft", gaps: [], gap_count: 0, errors: [], warnings: [] });
      }
      return undefined;
    };
    const { qc } = setup();
    await screen.findByDisplayValue("小兔子的交通工具書");
    editsQueueFor(qc, 1, 7, 5).push([stroke], [2244, 3272]);
    await userEvent.selectOptions(screen.getByLabelText("紙張"), "B5・家用印表機");
    await new Promise((r) => setTimeout(r, 50));
    expect(paperPatches()).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(paperPatches()).toHaveLength(1));
  });

  it("修線最後寫入失敗：不改紙張，說明原因", async () => {
    respond = async (method, url) =>
      method === "PUT" && url.includes("/lineart/edits") ? json({ detail: "伺服器壞了" }, 500) : undefined;
    const { qc } = setup();
    await screen.findByDisplayValue("小兔子的交通工具書");
    const e = editsQueueFor(qc, 1, 7, 5);
    e.push([stroke], [2244, 3272]);
    await e.flush().catch(() => {});
    await userEvent.selectOptions(screen.getByLabelText("紙張"), "B5・家用印表機");
    expect(await screen.findByText(/修線還沒存好/)).toBeInTheDocument();
    expect(paperPatches()).toHaveLength(0);
    expect(screen.getByLabelText("紙張")).toHaveValue("a4:home"); // 選單回到原本的紙張
  });
});

describe("② 主角", () => {
  it("書還沒建立就按產生：先建立書，再產生一張（不帶 confirm_change）", async () => {
    book = null;
    setup(null);
    await userEvent.type(screen.getByLabelText("書名"), "小兔子");
    await userEvent.type(screen.getByLabelText("主角長什麼樣子？"), "一隻圓圓的小白兔");
    await userEvent.click(screen.getByRole("button", { name: "＋ 產生主角" }));
    await waitFor(() => expect(posts("/api/books/1/character/candidates")).toHaveLength(1));
    expect(posts("/api/books")).toHaveLength(1);
    expect(calls.findIndex((c) => c.url === "/api/books")).toBeLessThan(
      calls.findIndex((c) => c.url === "/api/books/1/character/candidates"),
    );
    expect(posts("/api/books/1/character/candidates")[0].body).toEqual({ description: "一隻圓圓的小白兔" });
    // 排隊中的那張出現；畫好之前不能再按
    expect(await screen.findByText("排隊中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "正在畫…" })).toBeDisabled();
  });

  it("選這隻：設成主角，標上「選好了 ✓」，按鈕變「就是牠 ✓」", async () => {
    gens = [charGen(5), charGen(6)];
    setup();
    const pick = await screen.findAllByRole("button", { name: "選這隻" });
    expect(screen.getByRole("button", { name: "＋ 再畫一隻" })).toBeEnabled();
    let release!: () => void;
    respond = (method, url) =>
      method === "POST" && url === "/api/books/1/character"
        ? new Promise<undefined>((r) => (release = () => r(undefined)))
        : undefined;
    await userEvent.click(pick[1]);
    // 還在存：不能再畫一隻（不然不知道要帶 confirm_change）
    expect(screen.getByRole("button", { name: "＋ 再畫一隻" })).toBeDisabled();
    await act(async () => release());
    await waitFor(() => expect(posts("/api/books/1/character")).toHaveLength(1));
    expect(posts("/api/books/1/character")[0].body).toEqual({ generation_id: 6 });
    expect(await screen.findByRole("button", { name: "就是牠 ✓" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("選好了 ✓")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("已經有主角：選別隻先問「換主角？」；先不要就不送，換主角才帶 confirm_change", async () => {
    book = bookOf({ character_generation_id: 5 });
    gens = [charGen(5), charGen(6)];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "選這隻" }));
    const dialog = screen.getByRole("dialog", { name: "換主角？" });
    expect(dialog).toHaveTextContent("已經畫好的動作會保留");
    await userEvent.click(within(dialog).getByRole("button", { name: "先不要" }));
    expect(posts("/api/books/1/character")).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "選這隻" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "換主角？" })).getByRole("button", { name: "換主角" }));
    await waitFor(() => expect(posts("/api/books/1/character")).toHaveLength(1));
    expect(posts("/api/books/1/character")[0].body).toEqual({ generation_id: 6, confirm_change: true });
  });

  it("已經有主角時再畫一隻：不用確認（只是多一張候選），帶 confirm_change 給後端", async () => {
    book = bookOf({ character_generation_id: 5, character_description: "小白兔" });
    gens = [charGen(5)];
    setup();
    expect(await screen.findByLabelText("主角長什麼樣子？")).toHaveValue("小白兔");
    await userEvent.click(await screen.findByRole("button", { name: "＋ 再畫一隻" }));
    await waitFor(() => expect(posts("/api/books/1/character/candidates")).toHaveLength(1));
    expect(posts("/api/books/1/character/candidates")[0].body).toEqual({ description: "小白兔", confirm_change: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("↑ 上傳自己的主角：沒有主角直接上傳並選用；後端的驗證訊息照樣顯示", async () => {
    setup();
    const input = await screen.findByLabelText("上傳自己的主角");
    await userEvent.upload(input, png("me.png"));
    await waitFor(() => expect(posts("/api/books/1/character/upload")).toHaveLength(1));
    const form = posts("/api/books/1/character/upload")[0].body as FormData;
    expect((form.get("file") as File).name).toBe("me.png");
    expect(form.get("confirm_change")).toBeNull();
    expect(await screen.findByText("選好了 ✓")).toBeInTheDocument();

    respond = (_m, url) =>
      url === "/api/books/1/character/upload" ? json({ detail: "圖片像素太多（最多 6400 萬像素）" }, 422) : undefined;
    await userEvent.upload(input, png("big.png"));
    await userEvent.click(within(screen.getByRole("dialog", { name: "換主角？" })).getByRole("button", { name: "換主角" }));
    expect(await screen.findByText("圖片像素太多（最多 6400 萬像素）")).toBeInTheDocument();
    expect((posts("/api/books/1/character/upload")[1].body as FormData).get("confirm_change")).toBe("true");
  });
});

describe("③ 故事：預覽與每頁的背景", () => {
  it("貼上後即時列出每一頁：頁碼、場景・動作、圖下方的字", async () => {
    setup();
    await screen.findByLabelText("把故事貼在這裡");
    paste(STORY);
    expect(screen.getByText("讀到 3 段")).toBeInTheDocument();
    const rows = within(screen.getByRole("list", { name: "每一頁" })).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAccessibleName("第 1 頁");
    expect(rows[0]).toHaveTextContent("消防局門口・開著消防車揮手");
    expect(rows[0]).toHaveTextContent("中文：消防車");
    expect(rows[0]).toHaveTextContent("英文：Fire Engine");
    expect(rows[2]).toHaveTextContent("公園・開心地跳起來");
    expect(rows[2]).not.toHaveTextContent("中文：");
  });

  it("看不懂、超過字數的段落列在「不會匯入」", async () => {
    setup();
    await screen.findByLabelText("把故事貼在這裡");
    paste(`場景：${"x".repeat(801)}\n動作：跑\n\n場景：海\n動作：游\n\n希望對你有幫助！`);
    const note = screen.getByRole("note", { name: "不會匯入的段落" });
    expect(note).toHaveTextContent("第 1 段：場景超過 800 字");
    expect(note).toHaveTextContent("找不到「場景：」或「動作：」：希望對你有幫助！");
  });

  it("套用主題包「交通工具」：10 頁放進貼上框", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "套用主題包" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "套用主題包" })).getByRole("button", { name: /交通工具/ }));
    expect(screen.getByText("讀到 10 段")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "每一頁" })).getAllByRole("listitem")).toHaveLength(10);
  });

  it("套用主題包後開始做：新頁設成簡單線條", async () => {
    book = bookOf({ character_generation_id: 5 });
    setup();
    await screen.findByText("選好了 ✓");
    await userEvent.click(screen.getByRole("button", { name: "套用主題包" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "套用主題包" })).getByRole("button", { name: /交通工具/ }));
    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    const patches = calls.filter((c) => c.method === "PATCH" && c.url.startsWith("/api/pages/"));
    expect(patches).toHaveLength(10);
    for (const c of patches) expect(c.body).toMatchObject({ line_detail: "simple" });
  });

  it("套用動物園主題包：每頁主角放小、放在動物的另一邊（使用者 2026-11-20）", async () => {
    book = bookOf({ character_generation_id: 5 });
    setup();
    await screen.findByText("選好了 ✓");
    await userEvent.click(screen.getByRole("button", { name: "套用主題包" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "套用主題包" })).getByRole("button", { name: /動物園/ }));
    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    const puts = calls.filter((c) => c.method === "PUT" && c.url.endsWith("/placement"));
    expect(puts).toHaveLength(12);
    expect(puts[0].body).toEqual({ cx: 0.26, cy: 0.75, height: 0.42, flip: false });
    expect(puts[1].body).toEqual({ cx: 0.74, cy: 0.75, height: 0.42, flip: false });
  });

  it("交通工具主題包不帶位置：不存位置", async () => {
    book = bookOf({ character_generation_id: 5 });
    setup();
    await screen.findByText("選好了 ✓");
    await userEvent.click(screen.getByRole("button", { name: "套用主題包" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "套用主題包" })).getByRole("button", { name: /交通工具/ }));
    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    expect(calls.filter((c) => c.url.endsWith("/placement"))).toHaveLength(0);
  });

  it("套用主題包後自己改了文字：不再套用主題包的線條設定", async () => {
    book = bookOf({ character_generation_id: 5 });
    setup();
    await screen.findByText("選好了 ✓");
    await userEvent.click(screen.getByRole("button", { name: "套用主題包" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "套用主題包" })).getByRole("button", { name: /交通工具/ }));
    const box = screen.getByLabelText("把故事貼在這裡") as HTMLTextAreaElement;
    paste(box.value.replace("消防車", "大消防車"));
    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    const patches = calls.filter((c) => c.method === "PATCH" && c.url.startsWith("/api/pages/"));
    expect(patches.length).toBeGreaterThan(0);
    for (const c of patches) expect(c.body).not.toHaveProperty("line_detail");
  });

  it("每頁 AI 畫背景／↑ 上傳背景：選了圖片就算上傳（背景少一張），按 AI 畫背景改回來", async () => {
    book = bookOf({ character_generation_id: 5 });
    setup();
    await screen.findByLabelText("把故事貼在這裡");
    paste(STORY);
    const bar = screen.getByRole("region", { name: /會畫/ });
    expect(bar).toHaveTextContent("準備好了！會畫 6 張圖（背景 3＋動作 3）");
    const group = screen.getByRole("group", { name: "第 3 頁的背景" });
    const ai = within(group).getByRole("button", { name: "AI 畫背景" });
    expect(ai).toHaveAttribute("aria-pressed", "true");
    await userEvent.upload(within(group).getByLabelText("第 3 頁上傳背景"), png());
    expect(ai).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("listitem", { name: "第 3 頁" })).toHaveTextContent("（我上傳的 park.png）・開心地跳起來");
    expect(bar).toHaveTextContent("會畫 5 張圖（背景 2＋動作 3）");
    expect(calls.filter((c) => c.url.includes("/backgrounds/upload"))).toHaveLength(0); // 按開始才上傳
    await userEvent.click(ai);
    expect(ai).toHaveAttribute("aria-pressed", "true");
    expect(bar).toHaveTextContent("會畫 6 張圖（背景 3＋動作 3）");
  });

  it("已經有的書：列出現有頁，貼上的只填空欄位、多的接在後面；標題是「主角與故事」", async () => {
    book = bookOf({ character_generation_id: 5 });
    pages = [pageOf(11, 0, { scene_prompt: "舊的森林", action_prompt: "跑步", background_generation_id: 70 })];
    gens = [charGen(5), { ...charGen(70), kind: "background", page_id: 11, source: "upload" }];
    setup();
    expect(await screen.findByRole("heading", { level: 1, name: "主角與故事" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /整本書/ })).toHaveAttribute("href", "/books/1");
    const row1 = await screen.findByRole("listitem", { name: "第 1 頁" });
    expect(row1).toHaveTextContent("（我上傳的圖片）・跑步");
    expect(screen.getByText(/這本書有 1 頁/)).toBeInTheDocument();
    paste(STORY);
    expect(screen.getByRole("listitem", { name: "第 1 頁" })).toHaveTextContent("（我上傳的圖片）・跑步");
    expect(screen.getByRole("listitem", { name: "第 2 頁" })).toHaveTextContent("醫院門口・站在救護車旁邊");
    // 第 1 頁已有背景（上傳的），動作寫了但還沒畫 → 背景算第 2、3 頁，動作算 3 頁
    expect(screen.getByRole("region", { name: /會畫/ })).toHaveTextContent("會畫 5 張圖（背景 2＋動作 3）");
  });
});

describe("底部列", () => {
  beforeEach(() => {
    book = bookOf({ character_generation_id: 5 });
    gens = [charGen(5)];
  });

  it("張數、分鐘與今天的次數；放不下時說今天先做幾張", async () => {
    setup();
    await screen.findByLabelText("把故事貼在這裡");
    paste(STORY);
    const bar = await screen.findByRole("region", { name: /會畫/ });
    await waitFor(() => expect(bar).toHaveTextContent("（今天會用 6 次，還剩 60 次）"));
    expect(bar).toHaveTextContent("大約 3 分鐘。可以先去忙，好了會通知你");
    expect(bar).not.toHaveTextContent("明天繼續");
  });

  it("今天剩的次數不夠：今天先做 X 張，其餘明天繼續", async () => {
    plan = { backgrounds: 0, poses: 0, total: 0, estimate_s: 0, remaining: 4, fits: 0 };
    setup();
    await screen.findByLabelText("把故事貼在這裡");
    paste(STORY);
    const bar = await screen.findByRole("region", { name: /會畫/ });
    await waitFor(() => expect(bar).toHaveTextContent("今天先做 4 張，其餘明天繼續"));
    expect(bar).toHaveTextContent("（今天會用 4 次，還剩 4 次）");
  });

  it("沒有主角或沒有頁時不能開始", async () => {
    book = bookOf();
    setup();
    await screen.findByLabelText("把故事貼在這裡");
    paste(STORY);
    expect(startButton()).toBeDisabled();
    expect(screen.getByText("先選好主角，就可以開始。")).toBeInTheDocument();
  });

  it("有主角但沒有頁：不能開始，提示先貼上故事", async () => {
    setup();
    await screen.findByText("選好了 ✓");
    expect(startButton()).toBeDisabled();
    expect(screen.getByText("先貼上故事或加一頁，就可以開始。")).toBeInTheDocument();
    paste(STORY);
    expect(startButton()).toBeEnabled();
  });

  it("開始做這本書：先建立頁面、再上傳選好的背景、再 POST /produce，最後到 B；按鈕裡顯示進度", async () => {
    let release!: () => void;
    respond = (method, url) =>
      method === "POST" && /backgrounds\/upload$/.test(url)
        ? new Promise<Response>((r) => (release = () => r(json({}, 201))))
        : undefined;
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    await userEvent.upload(screen.getByLabelText("第 3 頁上傳背景"), png());
    await userEvent.click(startButton());
    expect(await screen.findByRole("button", { name: "上傳背景 1/1…" })).toBeDisabled();
    expect(posts("/api/books/1/produce")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));

    const seq = calls
      .filter((c) => c.method !== "GET")
      .map((c) => `${c.method} ${c.url}`);
    expect(seq).toEqual([
      "POST /api/books/1/pages",
      "PATCH /api/pages/101",
      "POST /api/books/1/pages",
      "POST /api/books/1/pages",
      "POST /api/pages/103/backgrounds/upload",
      "POST /api/books/1/produce",
    ]);
    expect(posts("/api/books/1/pages").map((c) => c.body)).toEqual([
      { scene_prompt: "消防局門口", action_prompt: "開著消防車揮手" },
      { scene_prompt: "醫院門口", action_prompt: "站在救護車旁邊" },
      { scene_prompt: "公園", action_prompt: "開心地跳起來" },
    ]);
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ caption_zh: "消防車", caption_en: "Fire Engine" });
  });

  it("今天的次數用完（429）：頁面建好了，照樣到 B", async () => {
    respond = (method, url) =>
      method === "POST" && url === "/api/books/1/produce" ? json({ detail: "今天的生成次數已用完，明天再來" }, 429) : undefined;
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    expect(screen.getByText("今天的生成次數已用完，明天再來")).toBeInTheDocument();
  });

  it("背景上傳失敗：不排 /produce、不離開，顯示後端的訊息；再按不會重複建立頁面", async () => {
    respond = (method, url) =>
      method === "POST" && /backgrounds\/upload$/.test(url) ? json({ detail: "看不懂這個圖片檔" }, 400) : undefined;
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    await userEvent.upload(screen.getByLabelText("第 2 頁上傳背景"), png());
    await userEvent.click(startButton());
    expect(await screen.findByText("第 2 頁的背景上傳失敗：看不懂這個圖片檔")).toBeInTheDocument();
    expect(posts("/api/books/1/produce")).toHaveLength(0);
    expect(push).not.toHaveBeenCalled();
    await waitFor(() => expect(startButton()).toBeEnabled());

    respond = () => undefined;
    await waitFor(() => expect(screen.getByRole("listitem", { name: "第 3 頁" })).toBeInTheDocument());
    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    expect(posts("/api/books/1/pages")).toHaveLength(3);
    expect(posts(/\/api\/pages\/102\/backgrounds\/upload$/)).toHaveLength(2);
  });

  it("其他錯誤：顯示訊息、留在這頁", async () => {
    respond = (method, url) =>
      method === "POST" && url === "/api/books/1/produce" ? json({ detail: "伺服器忙線" }, 500) : undefined;
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    await userEvent.click(startButton());
    expect(await screen.findByText("伺服器忙線")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("fix round 1", () => {
  beforeEach(() => {
    book = bookOf({ character_generation_id: 5 });
    gens = [charGen(5)];
  });

  it("上傳到一半失敗：已上傳好的頁照伺服器顯示（上傳的背景），張數不變", async () => {
    respond = (method, url) =>
      method === "POST" && url === "/api/pages/103/backgrounds/upload" ? json({ detail: "圖片太小" }, 422) : undefined;
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    await userEvent.upload(screen.getByLabelText("第 2 頁上傳背景"), png("a.png"));
    await userEvent.upload(screen.getByLabelText("第 3 頁上傳背景"), png("b.png"));
    const bar = screen.getByRole("region", { name: /會畫/ });
    expect(bar).toHaveTextContent("會畫 4 張圖（背景 1＋動作 3）");
    await userEvent.click(startButton());
    expect(await screen.findByText("第 3 頁的背景上傳失敗：圖片太小")).toBeInTheDocument();
    await waitFor(() => expect(startButton()).toBeEnabled());
    // 第 2 頁：伺服器已經用上傳的圖，畫面也是；第 3 頁的檔案還留著
    const row2 = screen.getByRole("listitem", { name: "第 2 頁" });
    expect(row2).toHaveTextContent("（我上傳的圖片）");
    expect(within(row2).getByRole("button", { name: "上傳背景" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("listitem", { name: "第 3 頁" })).toHaveTextContent("（我上傳的 b.png）");
    expect(bar).toHaveTextContent("會畫 4 張圖（背景 1＋動作 3）");
  });

  it("開始之後離開這頁：伺服器照做，但不會把人拉回 B", async () => {
    let release!: () => void;
    respond = (method, url) =>
      method === "POST" && /backgrounds\/upload$/.test(url)
        ? new Promise<Response>((r) => (release = () => r(json({}, 201))))
        : undefined;
    const { unmount } = setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    await userEvent.upload(screen.getByLabelText("第 3 頁上傳背景"), png());
    await userEvent.click(startButton());
    await screen.findByRole("button", { name: "上傳背景 1/1…" });
    unmount();
    await act(async () => release());
    await waitFor(() => expect(posts("/api/books/1/produce")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(push).not.toHaveBeenCalled();
  });

  it("頁面還在重抓時不能開始（快速重按不會重複建立頁面）", async () => {
    let release: (() => void) | null = null;
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    expect(startButton()).toBeEnabled();
    // /produce 失敗之後重抓頁面：抓回來之前不能再按
    respond = (method, url) => {
      if (method === "POST" && url === "/api/books/1/produce") return json({ detail: "伺服器忙線" }, 500);
      if (method === "GET" && url === "/api/books/1/pages" && posts("/api/books/1/produce").length > 0)
        return new Promise<undefined>((r) => (release = () => r(undefined)));
      return undefined;
    };
    await userEvent.click(startButton());
    expect(await screen.findByText("伺服器忙線")).toBeInTheDocument();
    await waitFor(() => expect(release).not.toBeNull());
    // 頁面抓回來之前：按鈕還在忙、不能按
    expect(screen.getByRole("button", { name: "排進去中…" })).toBeDisabled();
    await act(async () => release!());
    await waitFor(() => expect(startButton()).toBeEnabled());
  });

  it("＋ 加一頁：多一頁空白頁可以填；接在貼上的頁後面，開始時建立", async () => {
    setup();
    await screen.findByText("選好了 ✓");
    expect(startButton()).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "＋ 加一頁" }));
    const row1 = screen.getByRole("listitem", { name: "第 1 頁" });
    await userEvent.type(within(row1).getByRole("textbox", { name: "第 1 頁場景" }), "森林");
    await userEvent.type(within(row1).getByRole("textbox", { name: "第 1 頁動作" }), "跑步");
    expect(startButton()).toBeEnabled();
    expect(screen.getByRole("region", { name: /會畫/ })).toHaveTextContent("會畫 2 張圖（背景 1＋動作 1）");
    await userEvent.upload(within(row1).getByLabelText("第 1 頁上傳背景"), png("forest.png"));

    // 之後再貼故事：手動加的頁接在後面（上傳的圖跟著它走）
    paste(STORY);
    const row4 = screen.getByRole("listitem", { name: "第 4 頁" });
    expect(within(row4).getByRole("textbox", { name: "第 4 頁場景" })).toHaveValue("森林");
    expect(within(row4).getByRole("button", { name: "上傳背景" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("listitem", { name: "第 1 頁" })).toHaveTextContent("消防局門口");

    // 也可以刪掉再加回來
    await userEvent.click(screen.getByRole("button", { name: "＋ 加一頁" }));
    await userEvent.click(within(screen.getByRole("listitem", { name: "第 5 頁" })).getByRole("button", { name: "刪掉第 5 頁" }));
    expect(screen.queryByRole("listitem", { name: "第 5 頁" })).not.toBeInTheDocument();

    await userEvent.click(startButton());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1"));
    expect(posts("/api/books/1/pages").map((c) => c.body)).toEqual([
      { scene_prompt: "消防局門口", action_prompt: "開著消防車揮手" },
      { scene_prompt: "醫院門口", action_prompt: "站在救護車旁邊" },
      { scene_prompt: "公園", action_prompt: "開心地跳起來" },
      { scene_prompt: "森林", action_prompt: "跑步" },
    ]);
    expect(posts(/backgrounds\/upload$/).map((c) => c.url)).toEqual(["/api/pages/104/backgrounds/upload"]);
  });

  it("每頁的背景切換兩半都有 aria-pressed；底部列的張數會唸出來", async () => {
    setup();
    await screen.findByText("選好了 ✓");
    paste(STORY);
    const group = screen.getByRole("group", { name: "第 1 頁的背景" });
    const upload = within(group).getByRole("button", { name: "上傳背景" });
    expect(upload).toHaveAttribute("aria-pressed", "false");
    await userEvent.upload(within(group).getByLabelText("第 1 頁上傳背景"), png());
    expect(upload).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "AI 畫背景" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText(/^準備好了！會畫/).closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
  });
});
