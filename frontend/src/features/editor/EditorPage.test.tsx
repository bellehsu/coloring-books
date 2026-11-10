import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// ---------- 修線畫布換成可操作的替身（畫布本身另有測試） ----------
type LineProps = {
  lineartUrl: string;
  strokes: unknown[];
  onChange: (s: unknown[]) => void;
  toolbarExtra?: React.ReactNode;
  zoom?: number;
  pending?: boolean;
  gaps: unknown[];
  statusOverride?: React.ReactNode;
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
        {p.statusOverride ?? <p role="status">{p.gaps.length} 個缺口</p>}
      </div>
    ),
  };
});

// ---------- 合成面板與合成舞台換成替身：這裡只測 EditorPage 傳進去的回呼（背景／角色動作卡片用真的） ----------
type ComposePanelProps = import("./ComposePanel").ComposePanelProps;
type ComposeStageProps = import("./ComposeStage").ComposeStageProps;
vi.mock("./ComposePanel", () => ({
  ComposePanel: (p: ComposePanelProps) => (
    <section data-testid="compose-panel" data-placement={JSON.stringify(p.placement)} data-confirming={String(p.confirming)}>
      <button type="button" onClick={() => p.onPlacementChange({ ...p.placement, flip: !p.placement.flip })}>
        stub-flip
      </button>
      <button type="button" onClick={() => void p.onConfirm()}>
        stub-confirm
      </button>
    </section>
  ),
}));
vi.mock("./ComposeStage", () => ({
  ComposeStage: (p: ComposeStageProps) => (
    <div
      data-testid="compose-stage"
      data-bg={p.backgroundUrl ?? ""}
      data-cutout={p.cutoutUrl ?? ""}
      data-disabled={String(!!p.disabled)}
      data-placement={JSON.stringify(p.placement)}
    >
      <button type="button" onClick={() => p.onChange({ ...p.placement, cx: 0.25 })}>
        stub-move
      </button>
    </div>
  ),
}));

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
  action_prompt: `action ${id}`,
  controlnet_strength: 0.4,
  sketch_url: null,
  selected_generation_id: null,
  background_generation_id: null,
  pose_generation_id: null,
  placement: null,
  status: "draft",
  line_detail: "standard",
  line_width_px: 11,
  character_check: null,
  lineart_url: null,
  needs_fix: null,
  aspect_mismatch: false,
  compose_pending: false,
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
  mask_url: null,
  cutout_url: null,
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
/** POST /pages/{id}/backgrounds、/poses 的狀態碼（測 409）。 */
let genStatus: number;
/** `${method} ${url}` → 回傳這個錯誤狀態碼（測寫入失敗）。 */
let failWith: Record<string, number>;
/** POST /books/1/pages 回傳的新頁 id（測 id 被沿用）。 */
let nextPageId: number;
/** POST /books/1/pages 建立的新頁要多帶的欄位（測 id 被沿用時新頁已有素材）。 */
let newPageExtra: O;

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
  genStatus = 202;
  failWith = {};
  nextPageId = 10;
  newPageExtra = {};
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body =
        typeof init.body === "string" ? JSON.parse(init.body) : init.body instanceof FormData ? "form" : undefined;
      calls.push({ method, url, body });
      const g = gate[`${method} ${url}`];
      if (g) await g;
      const fail = failWith[`${method} ${url}`];
      if (fail) return j({ detail: "伺服器壞了" }, fail);
      if (url === "/api/books/1" && method === "GET") return book ? j(book) : j({ detail: "找不到這本繪本" }, 404);
      if (url === "/api/books/1/pages" && method === "GET") return j(pages);
      if (url === "/api/books/1/pages" && method === "POST") {
        const p = mkPage(nextPageId, { position: pages.length, scene_prompt: "", ...newPageExtra });
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
        if (q.get("kind")) list = list.filter((x) => x.kind === q.get("kind"));
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
      m = url.match(/^\/api\/pages\/(\d+)\/backgrounds\/upload$/);
      if (m) {
        const made = mkGen(70 + gens.length, { kind: "background", page_id: Number(m[1]), source: "upload" });
        gens = [...gens, made];
        pages = pages.map((p) => (p.id === Number(m![1]) ? { ...p, background_generation_id: made.id } : p));
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/(backgrounds|poses)$/);
      if (m) {
        if (genStatus !== 202) return j({ detail: "請先設定角色" }, genStatus);
        const kind = m[2] === "backgrounds" ? "background" : "pose";
        const made = mkGen(30 + gens.length, { kind, page_id: Number(m[1]), status: "queued", image_url: null, queue_position: 1 });
        gens = [...gens, made];
        return j(made, 202);
      }
      m = url.match(/^\/api\/pages\/(\d+)\/(background|pose)$/);
      if (m) {
        // 同後端：選了不同的背景／動作且選定圖是合成圖 → 清空選圖
        const gid = (body as { generation_id: number }).generation_id;
        const field = m[2] === "background" ? "background_generation_id" : "pose_generation_id";
        pages = pages.map((p) => {
          if (p.id !== Number(m![1])) return p;
          const sel = gens.find((g) => g.id === p.selected_generation_id);
          const clear = p[field] !== gid && sel?.kind === "composite";
          return { ...p, [field]: gid, ...(clear ? { selected_generation_id: null, status: "draft" } : {}) };
        });
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/placement$/);
      if (m) {
        pages = pages.map((p) => (p.id === Number(m![1]) ? { ...p, placement: body } : p));
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/compose$/);
      if (m) {
        const made = mkGen(50 + gens.length, { kind: "composite", page_id: Number(m[1]) });
        gens = [...gens, made];
        pages = pages.map((p) => (p.id === Number(m![1]) ? { ...p, selected_generation_id: made.id } : p));
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/select$/);
      if (m) return j({ detail: "合成圖不可用 /select" }, 500); // 新介面不應呼叫 /select
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

let rerenderEditor: () => void = () => {};
let lastQc: QueryClient;

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <EditorPage bookId={1} />
      </ToastProvider>
    </QueryClientProvider>
  );
  const r = render(tree());
  rerenderEditor = () => r.rerender(tree());
  lastQc = qc;
  return qc;
}

const pageButton = (n: number) => screen.getByRole("button", { name: new RegExp(`^第 ${n} 頁(?!的更多動作)`) });
const progressTitle = () => screen.getByRole("region", { name: "本頁進度" });
const calledWith = (method: string, url: string) => calls.filter((c) => c.method === method && c.url === url);

const composeTab = () => screen.getByRole("tab", { name: "① 構圖" });
const lineTab = () => screen.getByRole("tab", { name: /② 補線/ });
const bgCard = () => screen.getByRole("region", { name: "背景" });
const poseCard = () => screen.getByRole("region", { name: "角色動作" });
/** 有背景＋角色動作（含去背圖）的頁。 */
const composedReady = (id = 1, bg = 3, pose = 4) => {
  const o = { background_generation_id: bg, pose_generation_id: pose };
  const g = [
    mkGen(bg, { kind: "background", page_id: id }),
    mkGen(pose, { kind: "pose", page_id: id, mask_url: `/api/generations/${pose}/mask`, cutout_url: `/api/generations/${pose}/cutout` }),
  ];
  return { o, g };
};

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

  it("兩個步驟分頁；預設「① 構圖」：右邊是背景與角色動作卡片，沒有底部歷史托盤", async () => {
    setup();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["① 構圖", "② 補線"]);
    expect(composeTab()).toHaveAttribute("aria-selected", "true");
    expect(bgCard()).toBeInTheDocument();
    expect(poseCard()).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /歷史/ })).not.toBeInTheDocument();
    // 還沒有素材：舞台說明要先選
    expect(await screen.findByText("先選好背景和角色動作")).toBeInTheDocument();
  });

  it("進度清單 3 項：構圖、補線、完成", async () => {
    setup();
    const items = within(await screen.findByRole("region", { name: "本頁進度" })).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector("span + span")?.firstChild?.textContent)).toEqual(["構圖", "補線", "完成"]);
    expect(within(progressTitle()).getByText("還沒選背景")).toBeInTheDocument();
  });

  it("沒選圖時「② 補線」與「下一步：補線 →」停用並說明「請先擺好角色位置」；點了不會切換", async () => {
    setup();
    await screen.findAllByRole("tab");
    expect(composeTab()).not.toHaveAttribute("aria-disabled");
    expect(lineTab()).toHaveAttribute("aria-disabled", "true");
    expect(lineTab()).toHaveAccessibleDescription("請先擺好角色位置");
    const next = screen.getByRole("button", { name: "下一步：補線 →" });
    expect(next).toBeDisabled();
    expect(next).toHaveAccessibleDescription("請先擺好角色位置");
    await userEvent.click(lineTab());
    expect(composeTab()).toHaveAttribute("aria-selected", "true");
    expect(replace).not.toHaveBeenCalled();
  });

  it("只有背景或只有角色動作：舞台說明缺哪一個", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3 });
    pages[1] = mkPage(2, { pose_generation_id: 4 });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose", page_id: 2 })];
    setup();
    expect(await screen.findByText("先選好角色動作")).toBeInTheDocument();
    await userEvent.click(pageButton(2));
    expect(await screen.findByText("先選好背景")).toBeInTheDocument();
  });

  it("舊網址的模式：mode=background／pose → 構圖；mode=qa → 補線", async () => {
    search = "page=1&mode=pose";
    setup();
    await screen.findAllByRole("tab");
    expect(composeTab()).toHaveAttribute("aria-selected", "true");
    cleanup();
    search = "page=1&mode=background";
    setup();
    await screen.findAllByRole("tab");
    expect(composeTab()).toHaveAttribute("aria-selected", "true");
    cleanup();
    search = "page=1&mode=qa";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("linefix")).toBeInTheDocument();
    expect(lineTab()).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("qa-panel")).toBeInTheDocument();
  });

  it("legacy page keeps line tab：舊流程頁（選定圖是 page）可以補線；改選背景不清掉舊圖", async () => {
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5), mkGen(6, { kind: "background" })];
    setup();
    await screen.findAllByRole("tab");
    expect(lineTab()).not.toHaveAttribute("aria-disabled");
    await waitFor(() => expect(within(progressTitle()).getByText("舊流程圖片")).toBeInTheDocument());
    // 舊的候選圖（kind page）不出現在背景縮圖
    expect(await within(bgCard()).findAllByRole("radio")).toHaveLength(1);
    await userEvent.click(within(bgCard()).getByRole("radio", { name: "背景第 1 張" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1));
    await waitFor(() => expect(within(bgCard()).getByRole("radio", { name: "背景第 1 張" })).toHaveAttribute("aria-checked", "true"));
    expect(within(progressTitle()).getByText("舊流程圖片")).toBeInTheDocument();
    expect(lineTab()).not.toHaveAttribute("aria-disabled");
    await userEvent.click(lineTab());
    expect(await screen.findByTestId("linefix")).toBeInTheDocument();
    expect(calledWith("POST", "/api/pages/1/select")).toHaveLength(0);
  });

  it("有選圖：② 補線分頁顯示缺口數；切到補線顯示畫布、縮放、自動補線、線條設定與 QA 面板，網址帶 mode=line", async () => {
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    await screen.findAllByRole("tab");
    await waitFor(() => expect(within(lineTab()).getByText("3")).toBeInTheDocument());
    expect(lineTab()).not.toHaveAttribute("aria-disabled");
    await userEvent.click(lineTab());
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=1&mode=line", { scroll: false });
    const canvas = await screen.findByTestId("linefix");
    expect(canvas).toHaveAttribute("data-url", "/api/pages/1/lineart?v=a");
    expect(canvas).toHaveAttribute("data-gaps", "3");
    expect(screen.getByRole("group", { name: "縮放" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(canvas).toHaveAttribute("data-zoom", "1.5");
    expect(screen.getByRole("radiogroup", { name: "細緻度" })).toBeInTheDocument();
    expect(screen.getByTestId("qa-panel")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "背景" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /歷史/ })).not.toBeInTheDocument();
  });

  it("「下一步：補線 →」切到 ② 補線", async () => {
    const { o, g } = composedReady();
    pages[0] = mkPage(1, { ...o, selected_generation_id: 50 });
    gens = [...g, mkGen(50, { kind: "composite" })];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "下一步：補線 →" }));
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=1&mode=line", { scroll: false });
    expect(await screen.findByTestId("linefix")).toBeInTheDocument();
  });

  it("網址 mode=line 但沒選圖 → 構圖", async () => {
    search = "page=1&mode=line";
    setup();
    await screen.findAllByRole("tab");
    expect(composeTab()).toHaveAttribute("aria-selected", "true");
    expect(bgCard()).toBeInTheDocument();
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
    await userEvent.click(within(screen.getByTestId("linefix")).getByRole("button", { name: "自動補線" }));
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
    await userEvent.click(composeTab());
    await userEvent.click(lineTab());
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "2");
    await act(async () => release());
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(2));
    expect((calledWith("PUT", "/api/pages/1/lineart/edits")[1].body as { strokes: unknown[] }).strokes).toHaveLength(2);
  });

  it("背景卡片：點縮圖呼叫 POST /background（不是 /select），變成選用的；合成圖與角色動作不在背景縮圖裡", async () => {
    gens = [
      mkGen(5, { kind: "background" }),
      mkGen(6, { kind: "background" }),
      mkGen(7, { kind: "pose" }),
      mkGen(9, { kind: "composite" }),
    ];
    setup();
    await waitFor(() => expect(within(bgCard()).getAllByRole("radio")).toHaveLength(2));
    await userEvent.click(within(bgCard()).getByRole("radio", { name: "背景第 1 張" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/background")[0].body).toEqual({ generation_id: 5 });
    await waitFor(() => expect(within(bgCard()).getByRole("radio", { name: "背景第 1 張" })).toHaveAttribute("aria-checked", "true"));
    expect(within(bgCard()).getByText("AI 產生 · 第 1 張")).toBeInTheDocument();
    expect(calledWith("POST", "/api/pages/1/select")).toHaveLength(0);
  });

  it("角色動作卡片：點縮圖呼叫 POST /pose；「換一張」送出頁面的動作文字（精準）並顯示排隊中", async () => {
    gens = [mkGen(5, { kind: "background" }), mkGen(7, { kind: "pose", width: 832, height: 1216, mask_url: "/api/generations/7/mask" })];
    setup();
    await screen.findByRole("region", { name: "角色動作" });
    await userEvent.click(await within(poseCard()).findByRole("radio", { name: "角色動作第 1 張" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/pose")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/pose")[0].body).toEqual({ generation_id: 7 });
    await waitFor(() =>
      expect(within(poseCard()).getByRole("radio", { name: "角色動作第 1 張" })).toHaveAttribute("aria-checked", "true"),
    );
    await userEvent.click(within(poseCard()).getByRole("button", { name: "換一張角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "action 1", mode: "precise" });
    expect(await within(poseCard()).findByText("排隊第 1 位")).toBeInTheDocument();
  });

  it("構圖：舞台拿到背景與去背圖；移動 → PUT placement；確認合成（舞台停用）→ 切到 ② 補線", async () => {
    const { o, g } = composedReady();
    pages[0] = mkPage(1, o);
    gens = g;
    setup();
    const stage = await screen.findByTestId("compose-stage");
    expect(stage).toHaveAttribute("data-bg", "/img/3.png");
    expect(stage).toHaveAttribute("data-cutout", "/api/generations/4/cutout");
    expect(stage).toHaveAttribute("data-disabled", "false");
    expect(JSON.parse(stage.getAttribute("data-placement")!)).toEqual({ cx: 0.5, cy: 0.66, height: 0.6, flip: false });
    expect(within(progressTitle()).getByText("構圖")).toBeInTheDocument();
    let release!: () => void;
    gate["PUT /api/pages/1/placement"] = new Promise<void>((r) => (release = r));
    await userEvent.click(within(stage).getByRole("button", { name: "stub-move" }));
    // 畫面立即用新位置（不等伺服器）
    expect(JSON.parse(screen.getByTestId("compose-panel").getAttribute("data-placement")!)).toMatchObject({ cx: 0.25 });
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/placement")).toHaveLength(1));
    expect(calledWith("PUT", "/api/pages/1/placement")[0].body).toEqual({ cx: 0.25, cy: 0.66, height: 0.6, flip: false });
    // 位置還在送 → 確認合成要等它
    await userEvent.click(screen.getByRole("button", { name: "stub-confirm" }));
    expect(screen.getByTestId("compose-panel")).toHaveAttribute("data-confirming", "true");
    // 確認期間舞台不能改位置（合成結果＝畫面）
    expect(screen.getByTestId("compose-stage")).toHaveAttribute("data-disabled", "true");
    await act(async () => {});
    expect(calledWith("POST", "/api/pages/1/compose")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/compose")).toHaveLength(1));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=1&mode=line", { scroll: false }));
    expect(await screen.findByTestId("linefix")).toBeInTheDocument();
    await waitFor(() => expect(within(progressTitle()).getByText("已合成")).toBeInTheDocument());
    expect(calledWith("POST", "/api/pages/1/select")).toHaveLength(0);
  });

  it("確認合成中換到另一頁：只有正在合成的那頁停用，另一頁的舞台與確認按鈕照常可用", async () => {
    const a = composedReady(1, 3, 4);
    const b = composedReady(2, 5, 6);
    pages[0] = mkPage(1, a.o);
    pages[1] = mkPage(2, b.o);
    gens = [...a.g, ...b.g];
    let release!: () => void;
    gate["POST /api/pages/1/compose"] = new Promise<void>((r) => (release = r));
    setup();
    await screen.findByTestId("compose-stage");
    await userEvent.click(screen.getByRole("button", { name: "stub-confirm" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/compose")).toHaveLength(1));
    expect(screen.getByTestId("compose-panel")).toHaveAttribute("data-confirming", "true");
    expect(screen.getByTestId("compose-stage")).toHaveAttribute("data-disabled", "true");
    await userEvent.click(pageButton(2));
    await waitFor(() => expect(screen.getByTestId("compose-stage")).toHaveAttribute("data-bg", "/img/5.png"));
    expect(screen.getByTestId("compose-panel")).toHaveAttribute("data-confirming", "false");
    expect(screen.getByTestId("compose-stage")).toHaveAttribute("data-disabled", "false");
    // 第 2 頁照常可以確認合成
    await userEvent.click(screen.getByRole("button", { name: "stub-confirm" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/2/compose")).toHaveLength(1));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=2&mode=line", { scroll: false }));
    // 第 1 頁的合成較晚結束：不會把畫面切回第 1 頁
    await act(async () => release());
    await act(async () => {});
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=2&mode=line", { scroll: false });
  });

  it("compose_pending：進度清單的構圖顯示「待更新」", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 50, compose_pending: true });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose" }), mkGen(50, { kind: "composite" })];
    setup();
    await waitFor(() => expect(within(progressTitle()).getByText("待更新")).toBeInTheDocument());
  });

  it("構圖：位置從伺服器的草稿開始；翻轉 → PUT placement", async () => {
    pages[0] = mkPage(1, {
      background_generation_id: 3,
      pose_generation_id: 4,
      placement: { cx: 0.3, cy: 0.5, height: 0.4, flip: true },
    });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose", mask_url: "/m" })];
    setup();
    const stage = await screen.findByTestId("compose-stage");
    expect(JSON.parse(stage.getAttribute("data-placement")!)).toEqual({ cx: 0.3, cy: 0.5, height: 0.4, flip: true });
    await userEvent.click(screen.getByRole("button", { name: "stub-flip" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/placement")).toHaveLength(1));
    expect(calledWith("PUT", "/api/pages/1/placement")[0].body).toMatchObject({ flip: false });
  });

  it("位置寫入失敗時確認合成：不送 compose，提示位置尚未儲存", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose", mask_url: "/m" })];
    failWith["PUT /api/pages/1/placement"] = 500;
    setup();
    await userEvent.click(within(await screen.findByTestId("compose-stage")).getByRole("button", { name: "stub-move" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/placement")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "stub-confirm" }));
    await waitFor(() => expect(screen.getByText(/位置尚未儲存/)).toBeInTheDocument());
    expect(calledWith("POST", "/api/pages/1/compose")).toHaveLength(0);
    expect(screen.getByTestId("compose-panel")).toHaveAttribute("data-confirming", "false");
  });

  it("構圖：角色動作沒有遮罩（後端 cutout_url 為 null）→ 舞台拿不到去背圖（由舞台說明要重新產生）", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose", mask_url: null, cutout_url: null })];
    setup();
    const stage = await screen.findByTestId("compose-stage");
    expect(stage).toHaveAttribute("data-cutout", "");
    expect(stage).toHaveAttribute("data-bg", "/img/3.png");
  });

  it("構圖：背景沒有圖 → 舞台拿到 null（說明而不是一直載入中）", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(3, { kind: "background", image_url: null }), mkGen(4, { kind: "pose", mask_url: "/m", cutout_url: "/c" })];
    setup();
    const stage = await screen.findByTestId("compose-stage");
    expect(stage).toHaveAttribute("data-bg", "");
    expect(stage).toHaveAttribute("data-cutout", "/c");
  });

  it("構圖：選用的背景不在這頁的紀錄裡 → 說明，不是一直載入中", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(4, { kind: "pose", mask_url: "/m", cutout_url: "/c" })];
    setup();
    expect(await screen.findByText("找不到選用的背景或角色動作，請重新選一張")).toBeInTheDocument();
    expect(screen.queryByTestId("compose-stage")).not.toBeInTheDocument();
  });

  it("已合成的頁改選不同背景 → 選圖被清空，② 補線停用", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 9 });
    gens = [mkGen(3, { kind: "background" }), mkGen(8, { kind: "background" }), mkGen(4, { kind: "pose" }), mkGen(9, { kind: "composite" })];
    setup();
    await waitFor(() => expect(within(progressTitle()).getByText("已合成")).toBeInTheDocument());
    expect(lineTab()).not.toHaveAttribute("aria-disabled");
    await userEvent.click(await within(bgCard()).findByRole("radio", { name: "背景第 2 張" }));
    await waitFor(() => expect(lineTab()).toHaveAttribute("aria-disabled", "true"));
    expect(within(progressTitle()).queryByText("已合成")).not.toBeInTheDocument();
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

  it("背景「換一張」：POST backgrounds {style: simple}；排隊中顯示在背景卡片；後端 409 訊息顯示在 toast", async () => {
    setup();
    await screen.findByRole("region", { name: "背景" });
    await userEvent.click(within(bgCard()).getByRole("button", { name: "換一張背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/backgrounds")[0].body).toEqual({ style: "simple" });
    expect(await within(bgCard()).findByText("排隊第 1 位")).toBeInTheDocument();

    genStatus = 409;
    await userEvent.click(within(bgCard()).getByRole("button", { name: "換一張背景" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("請先設定角色");
  });

  it("上傳背景圖：POST upload 後變成本頁背景，縮圖有「上傳」標記", async () => {
    setup();
    await screen.findByRole("region", { name: "背景" });
    await userEvent.upload(
      within(bgCard()).getByLabelText("上傳背景圖"),
      new File(["png"], "mine.png", { type: "image/png" }),
    );
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds/upload")).toHaveLength(1));
    const thumb = await within(bgCard()).findByRole("radio", { name: "背景第 1 張（上傳的圖片）" });
    await waitFor(() => expect(thumb).toHaveAttribute("aria-checked", "true"));
    expect(within(bgCard()).getByText("上傳的圖片")).toBeInTheDocument();
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

  it("aspect_mismatch 時舞台上方提示", async () => {
    pages[0] = mkPage(1, { selected_generation_id: 5, aspect_mismatch: true });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByText("繪本尺寸已變更，這張圖的比例不同，建議重新生成")).toBeInTheDocument();
  });

  it("書沒有角色：提示連到角色工作室；構圖照常可用，角色動作「換一張」停用", async () => {
    book = mkBook({ character_generation_id: null, character_description: null });
    setup();
    await screen.findAllByRole("tab");
    expect(composeTab()).toHaveAttribute("aria-selected", "true");
    const links = screen.getAllByRole("link", { name: /角色工作室|先設定角色/ });
    expect(links[0]).toHaveAttribute("href", "/books/1/character");
    expect(within(poseCard()).getByRole("button", { name: "換一張角色動作" })).toBeDisabled();
    expect(within(bgCard()).getByRole("button", { name: "換一張背景" })).not.toBeDisabled();
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

  // ---------- fix round 1 ----------

  it("修線重新掛載時 edits 正在重抓 → 等它完成，不用舊快取 seed", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "0");
    await userEvent.click(composeTab());
    // 畫布不在時伺服器的修改圖層變了（例如 QA 面板自動補線）
    edits = [{ op: "draw", width_px: 11, points: [[0.1, 0.1], [0.2, 0.2]] }];
    let release!: () => void;
    gate["GET /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    await userEvent.click(lineTab());
    expect(screen.queryByTestId("linefix")).not.toBeInTheDocument();
    expect(screen.getByText("載入修線圖層中…")).toBeInTheDocument();
    await act(async () => release());
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "1");
  });

  it("改選背景前先等修線寫入完成（補線→構圖→點背景縮圖）", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5), mkGen(6, { kind: "background" })];
    let release!: () => void;
    gate["PUT /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    setup();
    await userEvent.click(within(await screen.findByTestId("linefix")).getByRole("button", { name: "stub-draw" }));
    await userEvent.click(composeTab());
    await userEvent.click(await within(bgCard()).findByRole("radio", { name: "背景第 1 張" }));
    await act(async () => {});
    expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1));
  });

  it("網址晚到：忽略我們較早寫的網址；外部改變才跟著改", async () => {
    setup();
    await screen.findByText("第 1 頁進度");
    await userEvent.click(pageButton(2));
    await userEvent.click(pageButton(3));
    search = "page=2"; // 第一次 replace 晚到
    rerenderEditor();
    expect(pageButton(3)).toHaveAttribute("aria-current", "true");
    search = "page=3";
    rerenderEditor();
    expect(pageButton(3)).toHaveAttribute("aria-current", "true");
    search = "page=1"; // 外部導覽
    rerenderEditor();
    expect(pageButton(1)).toHaveAttribute("aria-current", "true");
  });

  it("刪除目前頁：DELETE 完成前就選到相鄰頁", async () => {
    search = "page=2";
    gate["DELETE /api/pages/2"] = new Promise<void>(() => {});
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "第 2 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    expect(pageButton(3)).toHaveAttribute("aria-current", "true");
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=3", { scroll: false });
  });

  // ---------- fix round 2 ----------

  it("修線中自動補線：edits 背景重抓（pending）時畫布不卸載，完成後顯示「已補 N 條」", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "0");
    let release!: () => void;
    gate["GET /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    await userEvent.click(within(screen.getByTestId("linefix")).getByRole("button", { name: "自動補線" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/lineart/autofix")).toHaveLength(1));
    await waitFor(() => expect(calledWith("GET", "/api/pages/1/lineart/edits").length).toBeGreaterThan(1));
    expect(screen.getByTestId("linefix")).toBeInTheDocument();
    expect(screen.queryByText("載入修線圖層中…")).not.toBeInTheDocument();
    await act(async () => release());
    expect(await screen.findByText("已補 1 條")).toBeInTheDocument();
    expect(screen.getByTestId("linefix")).toHaveAttribute("data-strokes", "1");
  });

  it("修線中改線寬：edits 背景重抓（pending）時畫布不卸載", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    const canvas = await screen.findByTestId("linefix");
    await userEvent.click(within(canvas).getByRole("button", { name: "stub-draw" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(1));
    gate["GET /api/pages/1/lineart/edits"] = new Promise<void>(() => {});
    const slider = screen.getByLabelText(/線寬/);
    fireEvent.change(slider, { target: { value: "14" } });
    fireEvent.keyUp(slider, { key: "ArrowRight" });
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    await waitFor(() => expect(calledWith("GET", "/api/pages/1/lineart/edits").length).toBeGreaterThan(1));
    expect(screen.getByTestId("linefix")).toBe(canvas);
    expect(canvas).toHaveAttribute("data-strokes", "1");
    expect(screen.queryByText("載入修線圖層中…")).not.toBeInTheDocument();
  });

  it("改選背景前等修線寫入時連點兩次只送一次", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5), mkGen(6, { kind: "background" })];
    let release!: () => void;
    gate["PUT /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    setup();
    await userEvent.click(within(await screen.findByTestId("linefix")).getByRole("button", { name: "stub-draw" }));
    await userEvent.click(composeTab());
    const pick = await within(bgCard()).findByRole("radio", { name: "背景第 1 張" });
    await userEvent.click(pick);
    await userEvent.click(pick);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1));
    await act(async () => {});
    expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1);
  });

  // ---------- fix round 3 ----------

  it("A 畫線 → 切到 B（B 的 edits 載入中）→ 回到 A：A 顯示畫過的筆畫，不用舊的第一次 seed", async () => {
    // 先開 B：B 的 QA 有快取，之後切回 B 時 LineFixStage 不會因 QA 載入而卸載（同一個實例換 key）
    search = "page=2&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    pages[1] = mkPage(2, { selected_generation_id: 7 });
    gens = [mkGen(5), mkGen(7, { page_id: 2 })];
    setup();
    await screen.findByTestId("linefix");
    await userEvent.click(pageButton(1));
    await waitFor(() => expect(calledWith("GET", "/api/pages/1/lineart/edits")).toHaveLength(1));
    const a = await screen.findByTestId("linefix");
    expect(a).toHaveAttribute("data-strokes", "0");
    await userEvent.click(within(a).getByRole("button", { name: "stub-draw" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(1));
    await waitFor(() => expect(screen.getByTestId("linefix")).toHaveAttribute("data-gaps", "2")); // 寫入完成
    gate["GET /api/pages/2/lineart/edits"] = new Promise<void>(() => {});
    await userEvent.click(pageButton(2));
    expect(await screen.findByText("載入修線圖層中…")).toBeInTheDocument();
    await userEvent.click(pageButton(1));
    await waitFor(() => expect(screen.getByTestId("linefix")).toHaveAttribute("data-strokes", "1"));
  });

  // ---------- final review fixes ----------

  it("修線寫入失敗：缺口文字換成「修線尚未儲存」＋重試，重試送出最新筆畫；離開頁面會被攔", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    failWith["PUT /api/pages/1/lineart/edits"] = 500;
    setup();
    const canvas = await screen.findByTestId("linefix");
    await userEvent.click(within(canvas).getByRole("button", { name: "stub-draw" }));
    const failed = await screen.findByTestId("edits-save-failed");
    expect(failed).toHaveTextContent("修線尚未儲存");
    expect(within(screen.getByTestId("linefix")).queryByText(/個缺口/)).not.toBeInTheDocument();
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    delete failWith["PUT /api/pages/1/lineart/edits"];
    await userEvent.click(within(failed).getByRole("button", { name: "重試" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(2));
    expect((calledWith("PUT", "/api/pages/1/lineart/edits")[1].body as { strokes: unknown[] }).strokes).toHaveLength(1);
    await waitFor(() => expect(screen.queryByTestId("edits-save-failed")).not.toBeInTheDocument());
    expect(within(screen.getByTestId("linefix")).getByText("2 個缺口")).toBeInTheDocument();
  });

  // ---------- 刪除後 id 被沿用（後端舊庫沒有 AUTOINCREMENT 時）：舊頁的前端狀態不可串到新頁 ----------

  /** 第 3 頁：移動角色（PUT placement），再刪除它。 */
  async function editThenDeletePage3() {
    search = "page=3";
    const { o, g } = composedReady(3, 3, 4);
    pages[2] = mkPage(3, o);
    gens = g;
    newPageExtra = o; // 沿用 id 的新頁也有素材，才看得到舞台位置
    setup();
    await userEvent.click(within(await screen.findByTestId("compose-stage")).getByRole("button", { name: "stub-move" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/3/placement")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "第 3 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(calledWith("DELETE", "/api/pages/3")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: "第 3 頁的更多動作" })).not.toBeInTheDocument());
  }

  /** 新頁（沿用 id 3）：位置從預設開始、移動照常送出。 */
  async function expectFreshPage3() {
    await waitFor(() => expect(pageButton(3)).toHaveAttribute("aria-current", "true"));
    const stage = await screen.findByTestId("compose-stage");
    expect(JSON.parse(stage.getAttribute("data-placement")!)).toEqual({ cx: 0.5, cy: 0.66, height: 0.6, flip: false });
    await userEvent.click(within(stage).getByRole("button", { name: "stub-move" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/3/placement")).toHaveLength(2));
    expect(screen.queryByText(/位置儲存失敗|找不到頁面/)).not.toBeInTheDocument();
  }

  it("刪除最後一頁後新增，後端沿用同一個 id：位置照常存、從預設位置開始", async () => {
    nextPageId = 3;
    await editThenDeletePage3();
    await userEvent.click(screen.getByRole("button", { name: "新增頁面" }));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=3", { scroll: false }));
    await expectFreshPage3();
  });

  it("已刪除的 id 又出現在頁面清單（例如別的分頁新增）：清掉舊狀態", async () => {
    await editThenDeletePage3();
    pages = [...pages, mkPage(3, { position: pages.length, scene_prompt: "", ...newPageExtra })];
    await act(async () => {
      await lastQc.invalidateQueries({ queryKey: qk.pages(1) });
    });
    await userEvent.click(await screen.findByRole("button", { name: /^第 3 頁(?!的更多動作)/ }));
    await expectFreshPage3();
  });
});
