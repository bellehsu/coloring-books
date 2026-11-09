import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { qk } from "@/api/hooks";
import { ToastProvider } from "@/components/ui/Toast";
import { EditorPage } from "./EditorPage";

// ---------- next/navigation ----------
const replace = vi.fn();
const push = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push }),
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/books/1/editor",
}));

// ---------- 畫布換成可操作的替身（畫布本身在 Task 7/8 測過） ----------
const sketchSaves: ((b: Blob | null) => void)[] = [];
vi.mock("./SketchCanvas", async (orig) => {
  const real = await orig<typeof import("./SketchCanvas")>();
  return {
    ...real,
    SketchCanvas: (p: { initialUrl: string | null; onSave: (b: Blob | null) => void; width: number }) => {
      sketchSaves.push(p.onSave);
      return (
        <div data-testid="sketch" data-initial={p.initialUrl ?? ""} data-width={p.width}>
          <button type="button" onClick={() => p.onSave(new Blob(["x"], { type: "image/png" }))}>
            stub-save
          </button>
          <button type="button" onClick={() => p.onSave(null)}>
            stub-clear
          </button>
        </div>
      );
    },
  };
});
type LineProps = {
  lineartUrl: string;
  strokes: unknown[];
  onChange: (s: unknown[]) => void;
  toolbarExtra?: React.ReactNode;
  zoom?: number;
  pending?: boolean;
  gaps: unknown[];
};
vi.mock("./LineFixCanvas", async (orig) => {
  const real = await orig<typeof import("./LineFixCanvas")>();
  return {
    ...real,
    LineFixCanvas: (p: LineProps) => (
      <div data-testid="linefix" data-url={p.lineartUrl} data-strokes={p.strokes.length} data-zoom={p.zoom} data-gaps={p.gaps.length}>
        <button
          type="button"
          onClick={() => p.onChange([...p.strokes, { op: "draw", width_px: 11, points: [[0.5, 0.5]] }])}
        >
          stub-draw
        </button>
        {p.toolbarExtra}
      </div>
    ),
  };
});

// ---------- 假後端 ----------
type O = Record<string, unknown>;
const layout = {
  paper_mm: [216, 303],
  content_mm: [216, 303],
  content_px: [2551, 3579],
  generation_size: [864, 1216],
  safe_inset_mm: 6,
  page_px: [2551, 3579],
  content_offset_px: [0, 0],
  trim_box_px: [35, 35, 2516, 3544],
  safe_box_px: [106, 106, 2445, 3473],
  ratio_group: "a",
};
const mkBook = (o: O = {}): O => ({
  id: 1,
  title: "小兔子的一天",
  paper_size: "a4",
  print_mode: "print_shop",
  character_description: "bunny",
  character_generation_id: 99,
  layout,
  stats: { pages: 3, passed: 0, needs_fix: 0, no_selection: 3 },
  cover_url: null,
  created_at: "",
  updated_at: "",
  ...o,
});
const mkPage = (id: number, o: O = {}): O => ({
  id,
  book_id: 1,
  position: id - 1,
  scene_prompt: `scene ${id}`,
  controlnet_strength: 0.4,
  sketch_url: null,
  selected_generation_id: null,
  status: "draft",
  line_detail: "standard",
  line_width_px: 11,
  character_check: null,
  lineart_url: null,
  needs_fix: null,
  aspect_mismatch: false,
  created_at: "",
  updated_at: "",
  ...o,
});
const mkGen = (id: number, o: O = {}): O => ({
  id,
  book_id: 1,
  page_id: 1,
  kind: "page",
  status: "succeeded",
  discarded: false,
  prompt: "",
  user_text: "",
  seed: 100 + id,
  width: 864,
  height: 1216,
  image_url: `/img/${id}.png`,
  error: null,
  elapsed_s: 3,
  queue_position: null,
  progress: null,
  created_at: "",
  ...o,
});
const mkQA = (o: O = {}): O => ({
  status: "draft",
  errors: [],
  warnings: [],
  gaps: [
    [0.5, 0.2],
    [0.3, 0.4],
    [0.6, 0.7],
  ],
  gap_count: 3,
  dangling_count: 0,
  near_miss_count: 0,
  metrics: {},
  character_check: null,
  qa_note: "",
  forced_pass_reason: null,
  lineart_url: "/api/pages/1/lineart?v=a",
  ...o,
});

let book: O | null;
let pages: O[];
let gens: O[];
let edits: O[];
let calls: { method: string; url: string; body?: unknown }[];
let gate: Record<string, Promise<void> | undefined>;
let candidatesStatus: number;

const j = (data: unknown, status = 200) =>
  new Response(data === undefined ? null : JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });

beforeEach(() => {
  search = "";
  book = mkBook();
  pages = [mkPage(1), mkPage(2), mkPage(3)];
  gens = [];
  edits = [];
  calls = [];
  gate = {};
  candidatesStatus = 202;
  sketchSaves.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body =
        typeof init.body === "string" ? JSON.parse(init.body) : init.body instanceof FormData ? "form" : undefined;
      calls.push({ method, url, body });
      const g = gate[`${method} ${url}`];
      if (g) await g;
      if (url === "/api/books/1" && method === "GET") return book ? j(book) : j({ detail: "找不到這本繪本" }, 404);
      if (url === "/api/books/1/pages" && method === "GET") return j(pages);
      if (url === "/api/books/1/pages" && method === "POST") {
        const p = mkPage(10, { position: pages.length, scene_prompt: "" });
        pages = [...pages, p];
        return j(p, 201);
      }
      if (url === "/api/books/1/pages/order") {
        const ids = (body as { page_ids: number[] }).page_ids;
        pages = ids.map((id, i) => ({ ...pages.find((p) => p.id === id)!, position: i }));
        return j(pages);
      }
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0, running_id: null });
      if (url.startsWith("/api/books/1/generations")) {
        const q = new URL(url, "http://x").searchParams;
        let list = gens;
        if (q.get("page_id")) list = list.filter((x) => x.page_id === Number(q.get("page_id")));
        if (q.get("include_discarded") !== "true") list = list.filter((x) => !x.discarded);
        return j(list);
      }
      let m = url.match(/^\/api\/pages\/(\d+)$/);
      if (m && method === "PATCH") {
        pages = pages.map((p) => (p.id === Number(m![1]) ? { ...p, ...(body as O) } : p));
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      if (m && method === "DELETE") {
        pages = pages.filter((p) => p.id !== Number(m![1]));
        return j(undefined, 204);
      }
      m = url.match(/^\/api\/pages\/(\d+)\/sketch$/);
      if (m) {
        pages = pages.map((p) =>
          p.id === Number(m![1]) ? { ...p, sketch_url: method === "PUT" ? `/api/pages/${m![1]}/sketch?v=2` : null } : p,
        );
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/candidates$/);
      if (m) {
        if (candidatesStatus !== 202) return j({ detail: "請先設定角色" }, candidatesStatus);
        const made = [21, 22, 23, 24].map((id, i) =>
          mkGen(id, { page_id: Number(m![1]), status: "queued", image_url: null, queue_position: i }),
        );
        gens = [...gens, ...made];
        return j(made, 202);
      }
      m = url.match(/^\/api\/pages\/(\d+)\/select$/);
      if (m) {
        const gid = (body as { generation_id: number }).generation_id;
        pages = pages.map((p) => (p.id === Number(m![1]) ? { ...p, selected_generation_id: gid } : p));
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/qa$/);
      if (m) return j(mkQA({ lineart_url: `/api/pages/${m[1]}/lineart?v=a` }));
      m = url.match(/^\/api\/pages\/(\d+)\/lineart\/edits$/);
      if (m && method === "GET") {
        const p = pages.find((x) => x.id === Number(m![1]))!;
        return j({ generation_id: p.selected_generation_id, strokes: edits });
      }
      if (m && method === "PUT") {
        edits = (body as { strokes: O[] }).strokes;
        return j(mkQA({ gap_count: 2, gaps: [[0.1, 0.1], [0.2, 0.2]], lineart_url: "/api/pages/1/lineart?v=b" }));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/lineart\/autofix$/);
      if (m) {
        edits = [...edits, { op: "draw", width_px: 11, points: [[0.1, 0.1], [0.2, 0.2]] }];
        return j({ ...mkQA({ gap_count: 0, gaps: [], lineart_url: "/api/pages/1/lineart?v=c" }), autofix_added: 1 });
      }
      m = url.match(/^\/api\/generations\/(\d+)$/);
      if (m && method === "PATCH") {
        gens = gens.map((x) => (x.id === Number(m![1]) ? { ...x, ...(body as O) } : x));
        return j(gens.find((x) => x.id === Number(m![1])));
      }
      return j({ detail: "not found" }, 404);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
  push.mockReset();
});

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <EditorPage bookId={1} />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return qc;
}

const pageButton = (n: number) => screen.getByRole("button", { name: new RegExp(`^第 ${n} 頁(?!的更多動作)`) });
const progressTitle = () => screen.getByRole("region", { name: "本頁進度" });
const calledWith = (method: string, url: string) => calls.filter((c) => c.method === method && c.url === url);

describe("EditorPage", () => {
  it("沒有 ?page 時選第一頁", async () => {
    setup();
    expect(await screen.findByText("第 1 頁進度")).toBeInTheDocument();
    expect(pageButton(1)).toHaveAttribute("aria-current", "true");
  });

  it("從網址選頁；切頁更新網址（router.replace）", async () => {
    search = "page=2";
    setup();
    expect(await screen.findByText("第 2 頁進度")).toBeInTheDocument();
    expect(pageButton(2)).toHaveAttribute("aria-current", "true");
    await userEvent.click(pageButton(3));
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=3", { scroll: false });
    expect(within(progressTitle()).getByText("第 3 頁進度")).toBeInTheDocument();
    expect(pageButton(3)).toHaveAttribute("aria-current", "true");
  });

  it("網址的頁不存在 → 第一頁", async () => {
    search = "page=999";
    setup();
    expect(await screen.findByText("第 1 頁進度")).toBeInTheDocument();
  });

  it("沒選圖時「線稿與修線」「QA 檢查」停用並說明；點了不會切換", async () => {
    setup();
    const line = await screen.findByRole("tab", { name: /線稿與修線/ });
    const qa = screen.getByRole("tab", { name: /QA 檢查/ });
    expect(line).toHaveAttribute("aria-disabled", "true");
    expect(qa).toHaveAttribute("aria-disabled", "true");
    expect(line).toHaveAccessibleDescription("請先在下方候選圖中選一張");
    await userEvent.click(line);
    expect(screen.getByRole("tab", { name: "構圖草圖" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("sketch")).toBeInTheDocument();
  });

  it("有選圖：QA 分頁顯示缺口數；切到修線顯示畫布、縮放與自動補線，網址帶 mode", async () => {
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    const qaTab = await screen.findByRole("tab", { name: /QA 檢查/ });
    await waitFor(() => expect(within(qaTab).getByText("3")).toBeInTheDocument());
    const line = screen.getByRole("tab", { name: /線稿與修線/ });
    expect(line).not.toHaveAttribute("aria-disabled");
    await userEvent.click(line);
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=1&mode=line", { scroll: false });
    const canvas = await screen.findByTestId("linefix");
    expect(canvas).toHaveAttribute("data-url", "/api/pages/1/lineart?v=a");
    expect(canvas).toHaveAttribute("data-gaps", "3");
    expect(screen.getByRole("group", { name: "縮放" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(canvas).toHaveAttribute("data-zoom", "1.5");
    expect(screen.getByRole("radiogroup", { name: "細緻度" })).toBeInTheDocument();
  });

  it("網址 mode=line 但沒選圖 → 構圖模式", async () => {
    search = "page=1&mode=line";
    setup();
    expect(await screen.findByTestId("sketch")).toBeInTheDocument();
  });

  it("修線：每筆依序 PUT（帶 generation_id），回應更新 QA；自動補線顯示「已補 N 條」並併入筆畫", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    const canvas = await screen.findByTestId("linefix");
    await userEvent.click(within(canvas).getByRole("button", { name: "stub-draw" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(1));
    expect(calledWith("PUT", "/api/pages/1/lineart/edits")[0].body).toMatchObject({ generation_id: 5 });
    await waitFor(() => expect(screen.getByTestId("linefix")).toHaveAttribute("data-gaps", "2"));
    await userEvent.click(screen.getByRole("button", { name: "自動補線" }));
    expect(await screen.findByText("已補 1 條")).toBeInTheDocument();
    expect(calledWith("POST", "/api/pages/1/lineart/autofix")[0].body).toEqual({ generation_id: 5 });
    await waitFor(() => expect(screen.getByTestId("linefix")).toHaveAttribute("data-strokes", "2"));
    expect(screen.getByTestId("linefix")).toHaveAttribute("data-gaps", "0");
  });

  it("修線寫入中切走再回來：沿用同一個佇列，不從伺服器舊資料重設筆畫", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    let release!: () => void;
    gate["PUT /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    const qc = setup();
    const canvas = await screen.findByTestId("linefix");
    await userEvent.click(within(canvas).getByRole("button", { name: "stub-draw" }));
    await userEvent.click(within(screen.getByTestId("linefix")).getByRole("button", { name: "stub-draw" }));
    // 寫入還在路上時 edits 被重抓（例如改線寬）→ 快取變成伺服器的舊狀態（0 筆）
    await act(() => qc.refetchQueries({ queryKey: qk.edits(1) }));
    expect((qc.getQueryData(qk.edits(1)) as { strokes: unknown[] }).strokes).toHaveLength(0);
    await userEvent.click(screen.getByRole("tab", { name: "構圖草圖" }));
    await userEvent.click(screen.getByRole("tab", { name: /線稿與修線/ }));
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "2");
    await act(async () => release());
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(2));
    expect((calledWith("PUT", "/api/pages/1/lineart/edits")[1].body as { strokes: unknown[] }).strokes).toHaveLength(2);
  });

  it("候選圖：點選呼叫 select，出現「使用中」", async () => {
    gens = [mkGen(5), mkGen(6)];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 105 為本頁圖片" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/select")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/select")[0].body).toEqual({ generation_id: 5 });
    const btn = await screen.findByRole("button", { name: "選 seed 105 為本頁圖片" });
    await waitFor(() => expect(within(btn).getByText("使用中")).toBeInTheDocument());
  });

  it("Filmstrip 刪除：確認後 DELETE，選到相鄰頁", async () => {
    search = "page=2";
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "第 2 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    expect(calledWith("DELETE", "/api/pages/2")).toHaveLength(0);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(calledWith("DELETE", "/api/pages/2")).toHaveLength(1));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=3", { scroll: false }));
  });

  it("Filmstrip 下移：POST pages/order 帶完整 id 清單", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "第 1 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "下移" }));
    await waitFor(() => expect(calledWith("POST", "/api/books/1/pages/order")).toHaveLength(1));
    expect(calledWith("POST", "/api/books/1/pages/order")[0].body).toEqual({ page_ids: [2, 1, 3] });
  });

  it("新增頁面：POST 後自動選取新頁", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "新增頁面" }));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=10", { scroll: false }));
    expect(await screen.findByText("第 4 頁進度")).toBeInTheDocument();
  });

  it("產生 4 張候選：POST candidates {count: 4}；後端 409 訊息顯示在 toast", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "產生 4 張候選" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/candidates")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/candidates")[0].body).toEqual({ count: 4 });
    expect(await screen.findByText("排隊第 2 位")).toBeInTheDocument();

    candidatesStatus = 409;
    await userEvent.click(screen.getByRole("button", { name: "產生 4 張候選" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("請先設定角色");
  });

  it("產生前先存好正在編輯的場景描述", async () => {
    let release!: () => void;
    gate["PATCH /api/pages/1"] = new Promise<void>((r) => (release = r));
    setup();
    const ta = await screen.findByLabelText("場景描述");
    await userEvent.clear(ta);
    await userEvent.type(ta, "baking");
    await userEvent.click(screen.getByRole("button", { name: "產生 4 張候選" }));
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1);
    expect(calledWith("POST", "/api/pages/1/candidates")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/candidates")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1);
  });

  it("場景描述：離開欄位且有變更才 PATCH", async () => {
    setup();
    const ta = await screen.findByLabelText("場景描述");
    expect(ta).toHaveValue("scene 1");
    fireEvent.blur(ta);
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);
    await userEvent.type(ta, " park");
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);
    fireEvent.blur(ta);
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ scene_prompt: "scene 1 park" });
    expect(ta).toHaveAttribute("maxlength", "800");
  });

  it("構圖強度：放開時 PATCH", async () => {
    setup();
    const slider = await screen.findByLabelText(/構圖強度/);
    fireEvent.change(slider, { target: { value: "0.25" } });
    expect(screen.getByText("0.25")).toBeInTheDocument();
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);
    fireEvent.pointerUp(slider);
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ controlnet_strength: 0.25 });
  });

  it("線條設定：細緻度立即 PATCH；線寬顯示 mm、放開時 PATCH", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    await userEvent.click(await screen.findByRole("radio", { name: "細緻" }));
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")[0]?.body).toEqual({ line_detail: "detailed" }));
    expect(screen.getByText("11 px・約 0.9 mm")).toBeInTheDocument();
    const slider = screen.getByLabelText(/線寬/);
    fireEvent.change(slider, { target: { value: "16" } });
    expect(screen.getByText("16 px・約 1.4 mm")).toBeInTheDocument();
    fireEvent.keyUp(slider, { key: "ArrowRight" });
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")[1]?.body).toEqual({ line_width_px: 16 }));
  });

  it("構圖草圖存檔依呼叫順序：PUT 完成前的 DELETE 會等它", async () => {
    let release!: () => void;
    gate["PUT /api/pages/1/sketch"] = new Promise<void>((r) => (release = r));
    setup();
    const sk = await screen.findByTestId("sketch");
    expect(sk).toHaveAttribute("data-width", "864");
    await userEvent.click(within(sk).getByRole("button", { name: "stub-save" }));
    await userEvent.click(within(sk).getByRole("button", { name: "stub-clear" }));
    expect(calls.filter((c) => c.url === "/api/pages/1/sketch").map((c) => c.method)).toEqual(["PUT"]);
    await act(async () => release());
    await waitFor(() =>
      expect(calls.filter((c) => c.url === "/api/pages/1/sketch").map((c) => c.method)).toEqual(["PUT", "DELETE"]),
    );
  });

  it("換頁時草圖畫布以新頁重新掛載", async () => {
    pages[1] = mkPage(2, { sketch_url: "/api/pages/2/sketch?v=1" });
    setup();
    expect(await screen.findByTestId("sketch")).toHaveAttribute("data-initial", "");
    await userEvent.click(pageButton(2));
    expect(screen.getByTestId("sketch")).toHaveAttribute("data-initial", "/api/pages/2/sketch?v=1");
  });

  it("aspect_mismatch 時舞台上方提示", async () => {
    pages[0] = mkPage(1, { selected_generation_id: 5, aspect_mismatch: true });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByText("繪本尺寸已變更，這張圖的比例不同，建議重新生成")).toBeInTheDocument();
  });

  it("書沒有角色：產生停用並連到角色工作室", async () => {
    book = mkBook({ character_generation_id: null, character_description: null });
    setup();
    expect(await screen.findByRole("button", { name: "產生 4 張候選" })).toBeDisabled();
    const links = screen.getAllByRole("link", { name: /角色工作室/ });
    expect(links[0]).toHaveAttribute("href", "/books/1/character");
  });

  it("沒有頁面：空狀態，按新增頁面", async () => {
    pages = [];
    setup();
    expect(await screen.findByText("這本繪本還沒有頁面")).toBeInTheDocument();
    const btns = screen.getAllByRole("button", { name: "新增頁面" });
    await userEvent.click(btns[btns.length - 1]);
    await waitFor(() => expect(calledWith("POST", "/api/books/1/pages")).toHaveLength(1));
  });

  it("書不存在：訊息與回首頁連結", async () => {
    book = null;
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("找不到這本繪本");
    expect(screen.getByRole("link", { name: "回到我的繪本" })).toHaveAttribute("href", "/");
  });

  it("QA 模式放 QACompare／QAPanel 插槽，候選列隱藏", async () => {
    search = "page=1&mode=qa";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("qa-compare")).toBeInTheDocument();
    expect(screen.getByTestId("qa-panel")).toBeInTheDocument();
    expect(screen.queryByText("候選圖")).not.toBeInTheDocument();
  });
});
