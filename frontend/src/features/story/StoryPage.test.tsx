import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { StoryPage } from "./StoryPage";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

type O = Record<string, unknown>;
const page = (id: number, o: O = {}): O => ({
  id,
  book_id: 1,
  position: id,
  scene_prompt: "",
  action_prompt: "",
  background_generation_id: null,
  pose_generation_id: null,
  selected_generation_id: null,
  ...o,
});
const gen = (id: number, o: O = {}): O => ({
  id,
  book_id: 1,
  page_id: 1,
  kind: "background",
  status: "succeeded",
  discarded: false,
  source: "generated",
  image_url: `/api/files/${id}.png`,
  error: null,
  queue_position: null,
  progress: null,
  ...o,
});
const book = {
  id: 1,
  title: "小兔豆豆",
  paper_size: "a4",
  print_mode: "home",
  character_generation_id: 9,
  character_description: "bunny",
  stats: { pages: 3, passed: 0 },
};

let pages: O[];
let gens: O[];
let calls: { method: string; url: string; body?: unknown; form?: FormData }[];
let batchResponse: O;

const j = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  pages = [
    page(1, { scene_prompt: "溫暖的廚房", action_prompt: "捧著餅乾" }),
    page(2, { scene_prompt: "舊書堆", action_prompt: "" }),
    page(3, { scene_prompt: "", action_prompt: "跳起來" }),
  ];
  gens = [];
  calls = [];
  batchResponse = { queued: [], skipped: [] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const isForm = init.body instanceof FormData;
      const body = init.body && !isForm ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url, body, form: isForm ? (init.body as FormData) : undefined });
      if (url === "/api/books/1" && method === "GET") return j(book);
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0, running_id: null });
      if (url === "/api/books/1/pages" && method === "GET") return j(pages);
      if (url === "/api/books/1/pages" && method === "POST") {
        const made = page(pages.length + 1);
        pages = [...pages, made];
        return j(made, 201);
      }
      if (url.startsWith("/api/books/1/generations")) return j(gens);
      let m = url.match(/^\/api\/pages\/(\d+)$/);
      if (m && method === "PATCH") {
        pages = pages.map((p) => (p.id === Number(m![1]) ? { ...p, ...(body as O) } : p));
        return j(pages.find((p) => p.id === Number(m![1])));
      }
      m = url.match(/^\/api\/pages\/(\d+)\/backgrounds\/upload$/);
      if (m && method === "POST") return j(pages.find((p) => p.id === Number(m![1])), 201);
      if (url === "/api/books/1/materials/batch" && method === "POST") return j(batchResponse, 202);
      return j({ detail: "not found" }, 404);
    }),
  );
});

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <StoryPage bookId={1} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}
const post = (url: string) => calls.filter((c) => c.method === "POST" && c.url === url);
const patches = () => calls.filter((c) => c.method === "PATCH");

describe("StoryPage", () => {
  it("renders one row per page with scene and action", async () => {
    setup();
    expect(await screen.findByRole("textbox", { name: "第 1 頁場景" })).toHaveValue("溫暖的廚房");
    expect(screen.getByRole("textbox", { name: "第 1 頁角色動作" })).toHaveValue("捧著餅乾");
    expect(screen.getByRole("textbox", { name: "第 2 頁場景" })).toHaveValue("舊書堆");
    expect(screen.getByRole("textbox", { name: "第 3 頁角色動作" })).toHaveValue("跳起來");
    expect(screen.getAllByRole("article")).toHaveLength(3);
    expect(screen.getAllByRole("radiogroup", { name: "第 1 頁背景來源" })).toHaveLength(1);
    for (const chip of ["站著", "揮手", "跳起來", "拿著東西", "看書"]) {
      expect(within(screen.getAllByRole("article")[0]).getByRole("button", { name: chip })).toBeInTheDocument();
    }
  });

  it("toggle to 上傳圖片 shows upload zone and uploads on select", async () => {
    setup();
    const row = (await screen.findAllByRole("article"))[0];
    await userEvent.click(within(row).getByRole("radio", { name: "上傳圖片" }));
    expect(screen.queryByRole("textbox", { name: "第 1 頁場景" })).not.toBeInTheDocument();
    const input = within(row).getByLabelText("第 1 頁背景圖片") as HTMLInputElement;
    const file = new File(["x"], "草原.png", { type: "image/png" });
    await userEvent.upload(input, file);
    await waitFor(() => expect(post("/api/pages/1/backgrounds/upload")).toHaveLength(1));
    expect((post("/api/pages/1/backgrounds/upload")[0].form!.get("file") as File).name).toBe("草原.png");
    expect(await within(row).findByText("草原.png（已裁成頁面比例）")).toBeInTheDocument();
    // 切回 AI 產生，場景欄位回來
    await userEvent.click(within(row).getByRole("radio", { name: "AI 產生" }));
    expect(screen.getByRole("textbox", { name: "第 1 頁場景" })).toHaveValue("溫暖的廚房");
  });

  it("starts in 上傳圖片 when the selected background is an upload", async () => {
    gens = [gen(5, { source: "upload" })];
    pages[0] = page(1, { scene_prompt: "", action_prompt: "x", background_generation_id: 5 });
    setup();
    const row = (await screen.findAllByRole("article"))[0];
    await waitFor(() => expect(within(row).getByRole("radio", { name: "上傳圖片" })).toBeChecked());
    expect(within(row).getByText("已上傳圖片（已裁成頁面比例）")).toBeInTheDocument();
  });

  it("typing saves scene and action_prompt after debounce", async () => {
    setup();
    const scene = await screen.findByRole("textbox", { name: "第 2 頁場景" });
    fireEvent.change(scene, { target: { value: "舊書堆和蝸牛" } });
    fireEvent.change(screen.getByRole("textbox", { name: "第 2 頁角色動作" }), { target: { value: "抱著書" } });
    expect(patches()).toHaveLength(0);
    await waitFor(() => expect(patches()).toHaveLength(2), { timeout: 3000 });
    expect(patches().map((c) => c.body)).toEqual(
      expect.arrayContaining([{ scene_prompt: "舊書堆和蝸牛" }, { action_prompt: "抱著書" }]),
    );
    expect(patches().every((c) => c.url === "/api/pages/2")).toBe(true);
  });

  it("blur saves immediately, and only once", async () => {
    setup();
    const action = await screen.findByRole("textbox", { name: "第 2 頁角色動作" });
    fireEvent.change(action, { target: { value: "揮手" } });
    fireEvent.blur(action);
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].body).toEqual({ action_prompt: "揮手" });
    await new Promise((r) => setTimeout(r, 800));
    expect(patches()).toHaveLength(1);
  });

  it("a chip fills the action and saves it", async () => {
    setup();
    const row = (await screen.findAllByRole("article"))[1];
    await userEvent.click(within(row).getByRole("button", { name: "看書" }));
    expect(within(row).getByRole("textbox", { name: "第 2 頁角色動作" })).toHaveValue("看書");
    await waitFor(() => expect(patches().map((c) => c.body)).toEqual([{ action_prompt: "看書" }]));
  });

  it("batch button counts missing materials and is disabled at 0", async () => {
    // 第 1 頁：缺背景＋缺動作（2）；第 2 頁：缺背景（1）；第 3 頁：缺動作（1）→ 4
    setup();
    const btn = await screen.findByRole("button", { name: "產生全部素材（還有 4 張）" });
    expect(btn).toBeEnabled();
  });

  it("batch count skips pages with materials, queued jobs and upload rows", async () => {
    gens = [
      gen(5, { page_id: 1 }),
      gen(6, { page_id: 2, status: "queued", queue_position: 1, image_url: null }),
      gen(7, { page_id: 1, kind: "pose" }),
    ];
    pages[0] = page(1, { scene_prompt: "a", action_prompt: "b", background_generation_id: 5, pose_generation_id: 7 });
    setup();
    // 第 2 頁背景已排隊；第 3 頁缺動作 → 1
    expect(await screen.findByRole("button", { name: "產生全部素材（還有 1 張）" })).toBeEnabled();
    const rows = screen.getAllByRole("article");
    await userEvent.click(within(rows[2]).getByRole("radio", { name: "上傳圖片" }));
    const done = screen.getByRole("button", { name: "產生全部素材（還有 1 張）" });
    expect(done).toBeEnabled(); // 第 3 頁沒有場景，切上傳不影響動作
  });

  it("is disabled at 0 missing materials", async () => {
    pages = [page(1, { scene_prompt: "", action_prompt: "" })];
    setup();
    expect(await screen.findByRole("button", { name: "產生全部素材（還有 0 張）" })).toBeDisabled();
  });

  it("batch posts upload_page_ids and toasts the skips", async () => {
    batchResponse = {
      queued: [gen(40, { status: "queued", image_url: null })],
      skipped: [{ page_id: 2, position: 2, kind: "pose", reason: "沒有動作描述" }],
    };
    setup();
    const rows = await screen.findAllByRole("article");
    await userEvent.click(within(rows[1]).getByRole("radio", { name: "上傳圖片" }));
    await userEvent.click(screen.getByRole("button", { name: "產生全部素材（還有 3 張）" }));
    await waitFor(() => expect(post("/api/books/1/materials/batch")).toHaveLength(1));
    expect(post("/api/books/1/materials/batch")[0].body).toEqual({ upload_page_ids: [2] });
    expect(await screen.findByText(/已排入 1 張/)).toHaveTextContent("略過 1 項：第 2 頁 沒有動作描述");
  });

  it("status thumbnails show image, queued, running percent and retry", async () => {
    gens = [
      gen(5, { page_id: 1 }),
      gen(6, { page_id: 1, kind: "pose", status: "running", image_url: null, progress: [3, 10] }),
      gen(7, { page_id: 2, status: "queued", queue_position: 2, image_url: null }),
      gen(8, { page_id: 2, kind: "pose", status: "failed", image_url: null, error: "boom" }),
    ];
    pages[0] = page(1, { scene_prompt: "a", action_prompt: "b", background_generation_id: 5 });
    setup();
    const rows = await screen.findAllByRole("article");
    expect(await within(rows[0]).findByAltText("第 1 頁背景")).toHaveAttribute("src", "/api/files/5.png");
    expect(within(rows[0]).getByText("生成中 30%")).toBeInTheDocument();
    expect(within(rows[1]).getByText("排隊中")).toBeInTheDocument();
    await userEvent.click(within(rows[1]).getByRole("button", { name: "失敗，重試" }));
    await waitFor(() => expect(post("/api/books/1/materials/batch")).toHaveLength(1));
  });

  it("shows the progress banner only while jobs are active", async () => {
    gens = [gen(7, { page_id: 2, status: "queued", queue_position: 1, image_url: null })];
    setup();
    expect(
      await screen.findByText("正在產生素材，可以先去「頁面」擺已經好的頁面，不用等全部完成。"),
    ).toBeInTheDocument();
  });

  it("no banner when idle; 新增一頁 posts a page", async () => {
    setup();
    await screen.findAllByRole("article");
    expect(screen.queryByText(/正在產生素材/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "＋ 新增一頁" }));
    await waitFor(() => expect(post("/api/books/1/pages")).toHaveLength(1));
    expect(await screen.findAllByRole("article")).toHaveLength(4);
  });

  it("貼上整本故事 opens the import dialog", async () => {
    setup();
    await screen.findAllByRole("article");
    await userEvent.click(screen.getByRole("button", { name: "貼上整本故事" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
