import { useEffect, useRef } from "react";
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

// ---------- 畫布換成可操作的替身（畫布本身在 Task 7/8 測過） ----------
const sketchSaves: ((b: Blob | null) => void)[] = [];
vi.mock("./SketchCanvas", async (orig) => {
  const real = await orig<typeof import("./SketchCanvas")>();
  return {
    ...real,
    SketchCanvas: function StubSketch(p: {
      initialUrl: string | null;
      onSave: (b: Blob | null) => void;
      width: number;
      register?: (h: { flush(): void; dirty(): boolean }) => () => void;
    }) {
      sketchSaves.push(p.onSave);
      // 跟真的畫布一樣：落筆後 1 秒才存檔；register 的 flush 立即存、卸載時有未存變更也存
      const dirty = useRef(false);
      const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
      const saveRef = useRef(p.onSave);
      const registerRef = useRef(p.register);
      useEffect(() => {
        const flush = () => {
          clearTimeout(timer.current);
          if (!dirty.current) return;
          dirty.current = false;
          saveRef.current(new Blob(["drawn"], { type: "image/png" }));
        };
        const off = registerRef.current?.({ flush, dirty: () => dirty.current });
        return () => {
          flush();
          off?.();
        };
      }, []);
      const draw = () => {
        dirty.current = true;
        clearTimeout(timer.current);
        timer.current = setTimeout(() => {
          dirty.current = false;
          saveRef.current(new Blob(["drawn"], { type: "image/png" }));
        }, 1000);
      };
      return (
        <div data-testid="sketch" data-initial={p.initialUrl ?? ""} data-width={p.width}>
          <button type="button" onClick={draw}>
            stub-draw-sketch
          </button>
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

// ---------- Task 9–11 的面板與合成舞台換成替身：這裡只測 EditorPage 傳進去的回呼 ----------
type BgPanelProps = import("./BackgroundPanel").BackgroundPanelProps;
type PosePanelProps = import("./PosePanel").PosePanelProps;
type ComposePanelProps = import("./ComposePanel").ComposePanelProps;
type ComposeStageProps = import("./ComposeStage").ComposeStageProps;
type PoseStageProps = import("./PoseStage").PoseStageProps;
vi.mock("./PoseStage", () => ({
  PoseStage: (p: PoseStageProps) => <div data-testid="pose-stage" data-pose={p.pose?.id ?? ""} />,
}));
// realBgPanel = true 時用真的背景面板（產生所有背景要等面板裡的場景描述／構圖強度存好）
let realBgPanel = false;
vi.mock("./BackgroundPanel", async (orig) => {
  const real = await orig<typeof import("./BackgroundPanel")>();
  return {
    ...real,
    BackgroundPanel: (p: BgPanelProps) =>
      realBgPanel ? (
        <real.BackgroundPanel {...p} />
      ) : (
        <section data-testid="background-panel" data-page={p.page.id} data-active={p.activeCount}>
          <button
            type="button"
            disabled={p.generating}
            onClick={() => void p.beforeGenerate().then(p.onGenerate, () => {})}
          >
            stub-generate-background
          </button>
        </section>
      ),
  };
});
vi.mock("./PosePanel", () => ({
  PosePanel: (p: PosePanelProps) => (
    <section
      data-testid="pose-panel"
      data-active={p.activeCount}
      data-has-character={String(p.hasCharacter)}
      data-mode={p.mode}
      data-reason={p.preciseUnavailableReason ?? ""}
    >
      <button type="button" disabled={p.generating} onClick={() => void p.onGenerate("waving one hand", p.mode)}>
        stub-generate-pose
      </button>
      <button type="button" onClick={() => p.onModeChange("fast")}>
        stub-mode-fast
      </button>
    </section>
  ),
}));
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
/** GET /system/pose-modes 的回應。 */
let poseModes: O;

const j = (data: unknown, status = 200) =>
  new Response(data === undefined ? null : JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });

beforeEach(() => {
  search = "";
  realBgPanel = false;
  book = mkBook();
  pages = [mkPage(1), mkPage(2), mkPage(3)];
  gens = [];
  edits = [];
  calls = [];
  gate = {};
  genStatus = 202;
  failWith = {};
  nextPageId = 10;
  poseModes = { precise: { available: true, missing: [] }, fast: { available: true, missing: [] } };
  localStorage.clear();
  sketchSaves.length = 0;
  let blobN = 0;
  URL.createObjectURL = vi.fn(() => `blob:${++blobN}`);
  URL.revokeObjectURL = vi.fn();
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
        const p = mkPage(nextPageId, { position: pages.length, scene_prompt: "" });
        pages = [...pages, p];
        return j(p, 201);
      }
      if (url === "/api/books/1/pages/order") {
        const ids = (body as { page_ids: number[] }).page_ids;
        pages = ids.map((id, i) => ({ ...pages.find((p) => p.id === id)!, position: i }));
        return j(pages);
      }
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0, running_id: null });
      if (url === "/api/system/pose-modes") return j(poseModes);
      if (url.startsWith("/api/books/1/generations")) {
        const q = new URL(url, "http://x").searchParams;
        let list = gens;
        if (q.get("kind")) list = list.filter((x) => x.kind === q.get("kind"));
        if (q.get("page_id")) list = list.filter((x) => x.page_id === Number(q.get("page_id")));
        if (q.get("include_discarded") !== "true") list = list.filter((x) => !x.discarded);
        return j(list);
      }
      if (url === "/api/books/1/backgrounds/batch" && method === "POST") {
        // 同後端：沒場景描述、已有背景（選用或歷史裡成功未捨棄）、背景產生中 → 略過
        const queued: O[] = [];
        const skipped: O[] = [];
        for (const p of pages) {
          const bgs = gens.filter((g) => g.kind === "background" && g.page_id === p.id);
          const reason = !String(p.scene_prompt).trim()
            ? "沒有場景描述"
            : p.background_generation_id != null || bgs.some((g) => g.status === "succeeded" && !g.discarded)
              ? "已有背景"
              : bgs.some((g) => g.status === "queued" || g.status === "running")
                ? "背景產生中"
                : null;
          if (reason) {
            skipped.push({ page_id: p.id, position: p.position, reason });
            continue;
          }
          const made = mkGen(30 + gens.length, {
            kind: "background",
            page_id: p.id,
            status: "queued",
            image_url: null,
            queue_position: queued.length + 1,
          });
          gens = [...gens, made];
          queued.push(made);
        }
        return j({ queued, skipped }, 202);
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
      if (m && !pages.some((p) => p.id === Number(m![1]))) return j({ detail: "找不到頁面" }, 404);
      if (m) {
        pages = pages.map((p) =>
          p.id === Number(m![1]) ? { ...p, sketch_url: method === "PUT" ? `/api/pages/${m![1]}/sketch?v=2` : null } : p,
        );
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

  it("五個模式分頁；預設「背景」：舞台是背景（草圖畫板）、面板是背景面板、下方是背景歷史", async () => {
    setup();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["背景", "角色", "合成", "線稿與修線", "QA 檢查"]);
    expect(screen.getByRole("tab", { name: "背景" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("sketch")).toBeInTheDocument();
    expect(screen.getByTestId("background-panel")).toHaveAttribute("data-page", "1");
    expect(screen.getByRole("region", { name: "背景歷史" })).toBeInTheDocument();
  });

  it("進度清單 6 項", async () => {
    setup();
    const items = within(await screen.findByRole("region", { name: "本頁進度" })).getAllByRole("listitem");
    expect(items).toHaveLength(6);
    expect(items.map((li) => li.querySelector("span + span")?.firstChild?.textContent)).toEqual([
      "背景",
      "角色動作",
      "合成",
      "線條封閉",
      "角色外型",
      "通過 QA",
    ]);
  });

  it("沒有背景時「合成」停用並說明；沒選圖時「線稿與修線」「QA 檢查」停用並說明；點了不會切換", async () => {
    setup();
    const compose = await screen.findByRole("tab", { name: "合成" });
    const line = screen.getByRole("tab", { name: /線稿與修線/ });
    const qa = screen.getByRole("tab", { name: /QA 檢查/ });
    expect(screen.getByRole("tab", { name: "角色" })).not.toHaveAttribute("aria-disabled");
    expect(compose).toHaveAttribute("aria-disabled", "true");
    expect(compose).toHaveAccessibleDescription("請先選好背景與角色動作");
    expect(line).toHaveAttribute("aria-disabled", "true");
    expect(qa).toHaveAttribute("aria-disabled", "true");
    expect(line).toHaveAccessibleDescription("請先確認合成");
    await userEvent.click(compose);
    await userEvent.click(line);
    expect(screen.getByRole("tab", { name: "背景" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("sketch")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("只有背景、沒有角色動作 → 「合成」說明「請先選好角色動作」", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3 });
    setup();
    expect(await screen.findByRole("tab", { name: "合成" })).toHaveAccessibleDescription("請先選好角色動作");
  });

  it("只有角色動作 → 「合成」說明「請先選好背景」", async () => {
    pages[0] = mkPage(1, { pose_generation_id: 4 });
    setup();
    expect(await screen.findByRole("tab", { name: "合成" })).toHaveAccessibleDescription("請先選好背景");
  });

  it("legacy page keeps line/QA tabs：舊流程頁（選定圖是 page）線稿／QA 可用、合成停用；改選背景不清掉舊圖", async () => {
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5), mkGen(6, { kind: "background" })];
    setup();
    const line = await screen.findByRole("tab", { name: /線稿與修線/ });
    expect(line).not.toHaveAttribute("aria-disabled");
    expect(screen.getByRole("tab", { name: /QA 檢查/ })).not.toHaveAttribute("aria-disabled");
    expect(screen.getByRole("tab", { name: "合成" })).toHaveAttribute("aria-disabled", "true");
    await waitFor(() => expect(within(progressTitle()).getByText("舊流程圖片")).toBeInTheDocument());
    // 舊的候選圖（kind page）不出現在背景歷史
    expect(screen.queryByRole("button", { name: /seed 105/ })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 106 為本頁背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1));
    await waitFor(() => expect(within(progressTitle()).getByText("已選")).toBeInTheDocument());
    expect(within(progressTitle()).getByText("舊流程圖片")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /線稿與修線/ })).not.toHaveAttribute("aria-disabled");
    await userEvent.click(screen.getByRole("tab", { name: /線稿與修線/ }));
    expect(await screen.findByTestId("linefix")).toBeInTheDocument();
    expect(calledWith("POST", "/api/pages/1/select")).toHaveLength(0);
  });

  it("舊流程頁：網址 mode=qa 照常開 QA", async () => {
    search = "page=1&mode=qa";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("qa-compare")).toBeInTheDocument();
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

  it("網址 mode=line 但沒選圖 → 背景模式", async () => {
    search = "page=1&mode=line";
    setup();
    expect(await screen.findByTestId("sketch")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "背景" })).toHaveAttribute("aria-selected", "true");
  });

  it("網址 mode=compose 但沒有背景 → 背景模式", async () => {
    search = "page=1&mode=compose";
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
    await userEvent.click(screen.getByRole("tab", { name: "背景" }));
    await userEvent.click(screen.getByRole("tab", { name: /線稿與修線/ }));
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "2");
    await act(async () => release());
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/lineart/edits")).toHaveLength(2));
    expect((calledWith("PUT", "/api/pages/1/lineart/edits")[1].body as { strokes: unknown[] }).strokes).toHaveLength(2);
  });

  it("背景歷史：點選呼叫 POST /background（不是 /select），出現「使用中」", async () => {
    gens = [mkGen(5, { kind: "background" }), mkGen(6, { kind: "background" }), mkGen(7, { kind: "pose" })];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 105 為本頁背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/background")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/background")[0].body).toEqual({ generation_id: 5 });
    const btn = await screen.findByRole("button", { name: "選 seed 105 為本頁背景" });
    await waitFor(() => expect(within(btn).getByText("使用中")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /seed 107/ })).not.toBeInTheDocument();
    expect(calledWith("POST", "/api/pages/1/select")).toHaveLength(0);
  });

  it("角色模式：角色動作歷史、點選呼叫 POST /pose；產生送出 {action}", async () => {
    gens = [mkGen(5, { kind: "background" }), mkGen(7, { kind: "pose", width: 832, height: 1216, mask_url: "/api/generations/7/mask" })];
    setup();
    await userEvent.click(await screen.findByRole("tab", { name: "角色" }));
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=1&mode=pose", { scroll: false });
    expect(screen.getByRole("region", { name: "角色動作歷史" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "背景歷史" })).not.toBeInTheDocument();
    expect(screen.getByTestId("pose-panel")).toHaveAttribute("data-has-character", "true");
    expect(screen.getByTestId("pose-stage")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 107 為本頁角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/pose")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/pose")[0].body).toEqual({ generation_id: 7 });
    await waitFor(() => expect(screen.getByTestId("pose-stage")).toHaveAttribute("data-pose", "7"));
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-pose" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "waving one hand", mode: "precise" });
    await waitFor(() => expect(screen.getByTestId("pose-panel")).toHaveAttribute("data-active", "1"));
  });

  it("角色模式：精準不可用 → 退回快速並說明缺漏，產生送出 mode:fast（不寫入記憶）", async () => {
    search = "page=1&mode=pose";
    poseModes = {
      precise: { available: false, missing: ["ComfyUI-GGUF", "unet/qwen-image-edit-2511-Q4_K_M.gguf"] },
      fast: { available: true, missing: [] },
    };
    setup();
    const panel = await screen.findByTestId("pose-panel");
    await waitFor(() => expect(panel).toHaveAttribute("data-mode", "fast"));
    expect(panel).toHaveAttribute("data-reason", "精準模式缺少：ComfyUI-GGUF、unet/qwen-image-edit-2511-Q4_K_M.gguf");
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-pose" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "waving one hand", mode: "fast" });
    expect(localStorage.getItem("coloring.poseMode")).toBeNull();
  });

  it("角色模式：精準不可用但沒有缺漏清單 → 「精準模式目前無法使用」", async () => {
    search = "page=1&mode=pose";
    poseModes = { precise: { available: false, missing: [] }, fast: { available: true, missing: [] } };
    setup();
    const panel = await screen.findByTestId("pose-panel");
    await waitFor(() => expect(panel).toHaveAttribute("data-reason", "精準模式目前無法使用"));
    expect(panel).toHaveAttribute("data-mode", "fast");
  });

  it("角色模式：切到快速後重新掛載仍是快速（localStorage）", async () => {
    search = "page=1&mode=pose";
    setup();
    const panel = await screen.findByTestId("pose-panel");
    await waitFor(() => expect(calls.some((c) => c.url === "/api/system/pose-modes")).toBe(true));
    expect(panel).toHaveAttribute("data-mode", "precise");
    expect(panel).toHaveAttribute("data-reason", "");
    await userEvent.click(screen.getByRole("button", { name: "stub-mode-fast" }));
    expect(screen.getByTestId("pose-panel")).toHaveAttribute("data-mode", "fast");
    cleanup();
    setup();
    expect(await screen.findByTestId("pose-panel")).toHaveAttribute("data-mode", "fast");
  });

  it("合成模式：舞台拿到背景與去背圖；移動 → PUT placement；確認合成（舞台停用）→ 切到線稿與修線", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [
      mkGen(3, { kind: "background" }),
      mkGen(4, {
        kind: "pose",
        width: 832,
        height: 1216,
        mask_url: "/api/generations/4/mask",
        cutout_url: "/api/generations/4/cutout",
      }),
    ];
    setup();
    await waitFor(() => expect(within(progressTitle()).getByText("請確認合成")).toBeInTheDocument());
    const tab = screen.getByRole("tab", { name: "合成" });
    expect(tab).not.toHaveAttribute("aria-disabled");
    await userEvent.click(tab);
    expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=1&mode=compose", { scroll: false });
    const stage = await screen.findByTestId("compose-stage");
    expect(stage).toHaveAttribute("data-bg", "/img/3.png");
    expect(stage).toHaveAttribute("data-cutout", "/api/generations/4/cutout");
    expect(stage).toHaveAttribute("data-disabled", "false");
    expect(JSON.parse(stage.getAttribute("data-placement")!)).toEqual({ cx: 0.5, cy: 0.66, height: 0.6, flip: false });
    expect(screen.queryByRole("region", { name: /歷史/ })).not.toBeInTheDocument();
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
    search = "page=1&mode=compose";
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    pages[1] = mkPage(2, { background_generation_id: 5, pose_generation_id: 6 });
    const cut = (id: number, page_id: number) =>
      mkGen(id, { kind: "pose", page_id, mask_url: `/api/generations/${id}/mask`, cutout_url: `/api/generations/${id}/cutout` });
    gens = [mkGen(3, { kind: "background" }), cut(4, 1), mkGen(5, { kind: "background", page_id: 2 }), cut(6, 2)];
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

  it("compose_pending：進度清單的合成顯示「待重新合成」", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 50, compose_pending: true });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose" }), mkGen(50, { kind: "composite" })];
    setup();
    await waitFor(() => expect(within(progressTitle()).getByText("待重新合成")).toBeInTheDocument());
  });

  it("合成模式：位置從伺服器的草稿開始", async () => {
    search = "page=1&mode=compose";
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
    search = "page=1&mode=compose";
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

  it("合成模式：角色動作沒有遮罩（後端 cutout_url 為 null）→ 舞台拿不到去背圖（由舞台說明要重新產生）", async () => {
    search = "page=1&mode=compose";
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(3, { kind: "background" }), mkGen(4, { kind: "pose", mask_url: null, cutout_url: null })];
    setup();
    const stage = await screen.findByTestId("compose-stage");
    expect(stage).toHaveAttribute("data-cutout", "");
    expect(stage).toHaveAttribute("data-bg", "/img/3.png");
  });

  it("合成模式：背景沒有圖 → 舞台拿到 null（說明而不是一直載入中）", async () => {
    search = "page=1&mode=compose";
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(3, { kind: "background", image_url: null }), mkGen(4, { kind: "pose", mask_url: "/m", cutout_url: "/c" })];
    setup();
    const stage = await screen.findByTestId("compose-stage");
    expect(stage).toHaveAttribute("data-bg", "");
    expect(stage).toHaveAttribute("data-cutout", "/c");
  });

  it("合成模式：選用的背景不在這頁的紀錄裡 → 說明，不是一直載入中", async () => {
    search = "page=1&mode=compose";
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4 });
    gens = [mkGen(4, { kind: "pose", mask_url: "/m", cutout_url: "/c" })];
    setup();
    expect(await screen.findByText("找不到選用的背景或角色動作，請回到前面的步驟重新選擇")).toBeInTheDocument();
    expect(screen.queryByTestId("compose-stage")).not.toBeInTheDocument();
  });

  it("已合成的頁改選不同背景 → 選圖被清空，線稿／QA 停用", async () => {
    pages[0] = mkPage(1, { background_generation_id: 3, pose_generation_id: 4, selected_generation_id: 9 });
    gens = [mkGen(3, { kind: "background" }), mkGen(8, { kind: "background" }), mkGen(4, { kind: "pose" }), mkGen(9, { kind: "composite" })];
    setup();
    await waitFor(() => expect(within(progressTitle()).getByText("已合成")).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: /線稿與修線/ })).not.toHaveAttribute("aria-disabled");
    // 合成圖不出現在任何托盤
    expect(screen.queryByRole("button", { name: /seed 109/ })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 108 為本頁背景" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: /線稿與修線/ })).toHaveAttribute("aria-disabled", "true"));
    expect(within(progressTitle()).getByText("請確認合成")).toBeInTheDocument();
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

  it("產生背景：POST backgrounds；排隊中顯示在背景歷史；後端 409 訊息顯示在 toast", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "stub-generate-background" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1));
    expect(await screen.findByText("排隊第 1 位")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("background-panel")).toHaveAttribute("data-active", "1"));

    genStatus = 409;
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-background" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("請先設定角色");
  });

  it("產生所有背景：確認對話框列出頁數與略過的頁 → POST batch → toast；各頁歷史與排隊數更新", async () => {
    pages = [mkPage(1), mkPage(2), mkPage(3, { scene_prompt: "" })];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "產生所有背景" }));
    const dlg = screen.getByRole("dialog", { name: "產生所有背景？" });
    expect(dlg).toHaveTextContent("會為 2 頁產生背景（1 頁略過：第 3 頁沒有場景描述）");
    expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(0);
    await userEvent.click(within(dlg).getByRole("button", { name: "產生 2 頁背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(1));
    expect(await screen.findByText("已排入 2 頁背景、略過 1 頁")).toBeInTheDocument();
    // 本頁的歷史與排隊數、頁面清單的「生成中」都重抓
    expect(await screen.findByText("排隊第 1 位")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("background-panel")).toHaveAttribute("data-active", "1"));
    await waitFor(() => expect(within(pageButton(2)).getByText("生成中")).toBeInTheDocument());
    expect(within(pageButton(3)).queryByText("生成中")).not.toBeInTheDocument();
    // 都排入或略過了 → 再按也沒有可產生的頁
    await waitFor(() => expect(screen.getByRole("button", { name: "產生所有背景" })).toBeDisabled());
  });

  it("產生所有背景：取消不送出", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(0);
  });

  it("產生所有背景：已選背景、歷史裡有背景、背景產生中都不算 → 停用並說明", async () => {
    pages = [mkPage(1, { background_generation_id: 5 }), mkPage(2), mkPage(3)];
    gens = [
      mkGen(5, { kind: "background", page_id: 1 }),
      mkGen(6, { kind: "background", page_id: 2 }),
      mkGen(7, { kind: "background", page_id: 3, status: "queued", image_url: null, queue_position: 1 }),
    ];
    setup();
    const b = await screen.findByRole("button", { name: "產生所有背景" });
    await waitFor(() => expect(b).toBeDisabled());
    expect(b).toHaveAttribute("title", "沒有需要產生背景的頁面");
  });

  it("產生所有背景：先把畫板上未存的草圖存完（PUT 完成）才 POST batch", async () => {
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "stub-draw-sketch" }));
    await userEvent.click(screen.getByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(1));
    const order = calls.map((c) => `${c.method} ${c.url}`);
    expect(order.indexOf("PUT /api/pages/1/sketch")).toBeGreaterThan(-1);
    expect(order.indexOf("PUT /api/pages/1/sketch")).toBeLessThan(order.indexOf("POST /api/books/1/backgrounds/batch"));
  });

  it("產生所有背景：草圖存檔失敗就不送出，提示原因", async () => {
    failWith["PUT /api/pages/1/sketch"] = 500;
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "stub-draw-sketch" }));
    await userEvent.click(screen.getByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    expect(await screen.findByText("草圖或場景描述尚未儲存，沒有產生背景；請重試")).toBeInTheDocument();
    expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(0);
  });

  it("產生所有背景：送出中的場景描述 PATCH 完成後才 POST batch", async () => {
    realBgPanel = true;
    let release!: () => void;
    gate["PATCH /api/pages/1"] = new Promise<void>((r) => (release = r));
    setup();
    const box = await screen.findByLabelText("場景描述");
    await userEvent.clear(box);
    await userEvent.type(box, "森林");
    await userEvent.click(screen.getByRole("button", { name: "產生所有背景" })); // 離開欄位 → PATCH（卡住）
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    await act(async () => {});
    expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1);
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ scene_prompt: "森林" });
  });

  it("產生所有背景：本頁場景描述之前存失敗 → 先重送；再失敗就不送出並提示", async () => {
    realBgPanel = true;
    failWith["PATCH /api/pages/1"] = 500;
    setup();
    const box = await screen.findByLabelText("場景描述");
    await userEvent.clear(box);
    await userEvent.type(box, "森林");
    await userEvent.tab(); // 離開欄位 → PATCH 失敗
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    expect(await screen.findByText("草圖或場景描述尚未儲存，沒有產生背景；請重試")).toBeInTheDocument();
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(2);
    expect(calledWith("PATCH", "/api/pages/1")[1].body).toEqual({ scene_prompt: "森林" });
    expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(0);
    // 這次存成功 → 送出
    delete failWith["PATCH /api/pages/1"];
    await userEvent.click(screen.getByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(3);
  });

  it("產生所有背景：送出中的構圖強度 PATCH 完成後才 POST batch", async () => {
    realBgPanel = true;
    let release!: () => void;
    gate["PATCH /api/pages/1"] = new Promise<void>((r) => (release = r));
    setup();
    const slider = await screen.findByLabelText(/構圖強度/);
    fireEvent.change(slider, { target: { value: "0.2" } });
    fireEvent.keyUp(slider, { key: "ArrowLeft" });
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    await act(async () => {});
    expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/books/1/backgrounds/batch")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")).toEqual([
      expect.objectContaining({ body: { controlnet_strength: 0.2 } }),
    ]);
  });

  it("產生所有背景：後端錯誤顯示在 toast", async () => {
    failWith["POST /api/books/1/backgrounds/batch"] = 500;
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "產生所有背景" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "產生 3 頁背景" }));
    expect(await screen.findByText("伺服器壞了")).toBeInTheDocument();
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

  it("書沒有角色：「角色」分頁停用並說明，提示連到角色工作室；背景照常可用", async () => {
    book = mkBook({ character_generation_id: null, character_description: null });
    setup();
    const pose = await screen.findByRole("tab", { name: "角色" });
    expect(pose).toHaveAttribute("aria-disabled", "true");
    expect(pose).toHaveAccessibleDescription("請先在角色工作室設定角色");
    await userEvent.click(pose);
    expect(screen.getByRole("tab", { name: "背景" })).toHaveAttribute("aria-selected", "true");
    const links = screen.getAllByRole("link", { name: /角色工作室/ });
    expect(links[0]).toHaveAttribute("href", "/books/1/character");
    expect(screen.getByTestId("background-panel")).toBeInTheDocument();
  });

  it("書沒有角色：網址 mode=pose → 背景模式", async () => {
    search = "page=1&mode=pose";
    book = mkBook({ character_generation_id: null, character_description: null });
    setup();
    expect(await screen.findByTestId("background-panel")).toBeInTheDocument();
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

  it("QA 模式放 QACompare／QAPanel 插槽，歷史托盤隱藏", async () => {
    search = "page=1&mode=qa";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("qa-compare")).toBeInTheDocument();
    expect(screen.getByTestId("qa-panel")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /歷史/ })).not.toBeInTheDocument();
  });

  // ---------- fix round 1 ----------

  it("草圖 PUT 還沒完成就換頁再回來：畫布用待送的 blob seed，不用舊的 sketch_url", async () => {
    pages[0] = mkPage(1, { sketch_url: "/api/pages/1/sketch?v=1" });
    let release!: () => void;
    gate["PUT /api/pages/1/sketch"] = new Promise<void>((r) => (release = r));
    setup();
    const sk = await screen.findByTestId("sketch");
    expect(sk).toHaveAttribute("data-initial", "/api/pages/1/sketch?v=1");
    await userEvent.click(within(sk).getByRole("button", { name: "stub-save" }));
    await userEvent.click(pageButton(2));
    await userEvent.click(pageButton(1));
    expect(screen.getByTestId("sketch")).toHaveAttribute("data-initial", "blob:1");
    await act(async () => release());
  });

  it("清空草圖（DELETE 待送）後重新掛載 → 空白畫布", async () => {
    pages[0] = mkPage(1, { sketch_url: "/api/pages/1/sketch?v=1" });
    gate["DELETE /api/pages/1/sketch"] = new Promise<void>(() => {});
    setup();
    await userEvent.click(within(await screen.findByTestId("sketch")).getByRole("button", { name: "stub-clear" }));
    await userEvent.click(pageButton(2));
    await userEvent.click(pageButton(1));
    expect(screen.getByTestId("sketch")).toHaveAttribute("data-initial", "");
  });

  it("修線重新掛載時 edits 正在重抓 → 等它完成，不用舊快取 seed", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5)];
    setup();
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "0");
    await userEvent.click(screen.getByRole("tab", { name: "背景" }));
    // 畫布不在時伺服器的修改圖層變了（例如 QA 面板自動補線）
    edits = [{ op: "draw", width_px: 11, points: [[0.1, 0.1], [0.2, 0.2]] }];
    let release!: () => void;
    gate["GET /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    await userEvent.click(screen.getByRole("tab", { name: /線稿與修線/ }));
    expect(screen.queryByTestId("linefix")).not.toBeInTheDocument();
    expect(screen.getByText("載入修線圖層中…")).toBeInTheDocument();
    await act(async () => release());
    expect(await screen.findByTestId("linefix")).toHaveAttribute("data-strokes", "1");
  });

  it("改選背景前先等修線寫入完成（修線→背景分頁→點背景）", async () => {
    search = "page=1&mode=line";
    pages[0] = mkPage(1, { selected_generation_id: 5 });
    gens = [mkGen(5), mkGen(6, { kind: "background" })];
    let release!: () => void;
    gate["PUT /api/pages/1/lineart/edits"] = new Promise<void>((r) => (release = r));
    setup();
    await userEvent.click(within(await screen.findByTestId("linefix")).getByRole("button", { name: "stub-draw" }));
    await userEvent.click(screen.getByRole("tab", { name: "背景" }));
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 106 為本頁背景" }));
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
    await userEvent.click(screen.getByRole("button", { name: "自動補線" }));
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
    await userEvent.click(screen.getByRole("tab", { name: "背景" }));
    const pick = await screen.findByRole("button", { name: "選 seed 106 為本頁背景" });
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

  it("畫完 1 秒內就按產生背景：先把草圖存完（PUT 完成）才 POST backgrounds", async () => {
    let release!: () => void;
    gate["PUT /api/pages/1/sketch"] = new Promise<void>((r) => (release = r));
    setup();
    await userEvent.click(within(await screen.findByTestId("sketch")).getByRole("button", { name: "stub-draw-sketch" }));
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-background" }));
    // 沒等 debounce：按下產生就立即送出草圖
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/sketch")).toHaveLength(1));
    await act(async () => {});
    expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(0);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1));
    const order = calls.map((c) => `${c.method} ${c.url}`);
    expect(order.indexOf("PUT /api/pages/1/sketch")).toBeLessThan(order.indexOf("POST /api/pages/1/backgrounds"));
    expect(calledWith("PUT", "/api/pages/1/sketch")).toHaveLength(1); // debounce 不再重送
  });

  it("草圖存檔失敗：不產生、提示；畫布下方持續顯示「草圖尚未儲存」，按重試再送一次", async () => {
    failWith["PUT /api/pages/1/sketch"] = 500;
    setup();
    await userEvent.click(within(await screen.findByTestId("sketch")).getByRole("button", { name: "stub-draw-sketch" }));
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-background" }));
    expect(await screen.findByText(/草圖尚未儲存，沒有產生背景/)).toBeInTheDocument();
    expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(0);
    const line = screen.getByTestId("sketch-save-failed");
    expect(line).toHaveTextContent("草圖尚未儲存");
    delete failWith["PUT /api/pages/1/sketch"];
    await userEvent.click(within(line).getByRole("button", { name: "重試" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/sketch")).toHaveLength(2));
    await waitFor(() => expect(screen.queryByTestId("sketch-save-failed")).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-background" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1));
  });

  it("beforeunload：草圖還在送或畫布有未存變更時攔下離開；都存好就不攔", async () => {
    let release!: () => void;
    gate["PUT /api/pages/1/sketch"] = new Promise<void>((r) => (release = r));
    setup();
    const unload = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    const sk = await screen.findByTestId("sketch");
    expect(unload()).toBe(false);
    await userEvent.click(within(sk).getByRole("button", { name: "stub-draw-sketch" }));
    expect(unload()).toBe(true); // debounce 中
    await userEvent.click(within(sk).getByRole("button", { name: "stub-save" }));
    expect(unload()).toBe(true); // PUT 送出中
    await act(async () => release());
    await waitFor(() => expect(calledWith("PUT", "/api/pages/1/sketch").length).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100)); // 讓 debounce 的存檔也送完
    });
    await waitFor(() => expect(unload()).toBe(false));
  });

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

  it("刪除目前頁：未存的草圖不在卸載時送出，也不出現「草圖儲存失敗」", async () => {
    search = "page=2";
    let releaseDelete!: () => void;
    gate["DELETE /api/pages/2"] = new Promise<void>((r) => (releaseDelete = r));
    setup();
    await userEvent.click(within(await screen.findByTestId("sketch")).getByRole("button", { name: "stub-draw-sketch" }));
    await userEvent.click(screen.getByRole("button", { name: "第 2 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    expect(pageButton(3)).toHaveAttribute("aria-current", "true");
    await act(async () => releaseDelete());
    await waitFor(() => expect(calledWith("DELETE", "/api/pages/2")).toHaveLength(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1100));
    });
    expect(calledWith("PUT", "/api/pages/2/sketch")).toHaveLength(0);
    expect(screen.queryByText(/草圖儲存失敗/)).not.toBeInTheDocument();
  });

  it("刪除目前頁：刪除前就在路上的草圖 PUT 回 404 → 不提示錯誤", async () => {
    search = "page=2";
    let releasePut!: () => void;
    gate["PUT /api/pages/2/sketch"] = new Promise<void>((r) => (releasePut = r));
    setup();
    await userEvent.click(within(await screen.findByTestId("sketch")).getByRole("button", { name: "stub-save" }));
    await userEvent.click(screen.getByRole("button", { name: "第 2 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(calledWith("DELETE", "/api/pages/2")).toHaveLength(1));
    await act(async () => releasePut()); // 頁面已刪：404
    await act(async () => {});
    expect(screen.queryByText(/找不到頁面|草圖儲存失敗/)).not.toBeInTheDocument();
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(false);
  });

  // ---------- 刪除後 id 被沿用（後端舊庫沒有 AUTOINCREMENT 時）：舊頁的前端狀態不可串到新頁 ----------

  /** 第 3 頁：存草圖（PUT），再刪除它。（場景描述的部分由 Task 9 的背景面板測） */
  async function editThenDeletePage3() {
    search = "page=3";
    setup();
    await userEvent.click(within(await screen.findByTestId("sketch")).getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/3/sketch")).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "第 3 頁的更多動作" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "刪除…" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(calledWith("DELETE", "/api/pages/3")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: "第 3 頁的更多動作" })).not.toBeInTheDocument());
  }

  /** 新頁（沿用 id 3）：草圖照常送出、產生背景前先存好草圖。 */
  async function expectFreshPage3() {
    await waitFor(() => expect(pageButton(3)).toHaveAttribute("aria-current", "true"));
    expect(screen.getByTestId("sketch")).toHaveAttribute("data-initial", "");
    await userEvent.click(within(screen.getByTestId("sketch")).getByRole("button", { name: "stub-save" }));
    await waitFor(() => expect(calledWith("PUT", "/api/pages/3/sketch")).toHaveLength(2));
    await userEvent.click(screen.getByRole("button", { name: "stub-generate-background" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/3/backgrounds")).toHaveLength(1));
    expect(screen.queryByText(/草圖儲存失敗|找不到頁面/)).not.toBeInTheDocument();
  }

  it("刪除最後一頁後新增，後端沿用同一個 id：草圖照常存、畫板從空白開始", async () => {
    nextPageId = 3;
    await editThenDeletePage3();
    await userEvent.click(screen.getByRole("button", { name: "新增頁面" }));
    await waitFor(() => expect(replace).toHaveBeenLastCalledWith("/books/1/editor?page=3", { scroll: false }));
    await expectFreshPage3();
  });

  it("已刪除的 id 又出現在頁面清單（例如別的分頁新增）：清掉舊狀態", async () => {
    await editThenDeletePage3();
    pages = [...pages, mkPage(3, { position: pages.length, scene_prompt: "" })];
    await act(async () => {
      await lastQc.invalidateQueries({ queryKey: qk.pages(1) });
    });
    await userEvent.click(await screen.findByRole("button", { name: /^第 3 頁(?!的更多動作)/ }));
    await expectFreshPage3();
  });

});
