import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toast";
import { CharacterStudio } from "./CharacterStudio";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

type G = Record<string, unknown>;
const gen = (id: number, o: G = {}): G => ({
  id,
  book_id: 1,
  page_id: null,
  kind: "character",
  status: "succeeded",
  discarded: false,
  prompt: `single full body front view of bunny ${id}, standing`,
  user_text: `bunny ${id}`,
  seed: 100 + id,
  width: 832,
  height: 1216,
  image_url: `/api/files/${id}.png`,
  error: null,
  elapsed_s: 3,
  queue_position: null,
  progress: null,
  created_at: "2026-11-09T00:00:00Z",
  ...o,
});
const book = (o: G = {}): G => ({
  id: 1,
  title: "小兔子的一天",
  paper_size: "a4",
  print_mode: "print_shop",
  character_generation_id: null,
  character_description: null,
  stats: { pages: 0, passed: 0 },
  ...o,
});

let gens: G[];
let bk: G;
let calls: { method: string; url: string; body?: unknown }[];
let setCharacterResponse: () => Response;

const j = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  gens = [];
  bk = book();
  calls = [];
  setCharacterResponse = () => j({ ...bk, character_generation_id: 2 });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = init.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url, body });
      if (url === "/api/books/1" && method === "GET") return j(bk);
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0, running_id: null });
      if (url.startsWith("/api/books/1/generations")) return j(gens);
      if (url === "/api/books/1/character/candidates") {
        // Phase 9：一次一張（後端忽略 count）
        const made = gen(11 + gens.length, { status: "queued", image_url: null, queue_position: 0, user_text: body.description });
        gens = [...gens, made];
        return j([made], 201);
      }
      if (url === "/api/books/1/character" && method === "POST") return setCharacterResponse();
      const m = url.match(/^\/api\/generations\/(\d+)$/);
      if (m && method === "PATCH") {
        gens = gens.map((g) => (g.id === Number(m[1]) ? { ...g, discarded: body.discarded } : g));
        return j(gens.find((g) => g.id === Number(m[1])));
      }
      return j({ detail: "not found" }, 404);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  push.mockReset();
});

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <CharacterStudio bookId={1} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("CharacterStudio", () => {
  it("沒有候選時按鈕是「產生角色」：送出描述（不帶 count），出現 1 張排隊卡", async () => {
    setup();
    const ta = await screen.findByLabelText("描述角色的外型");
    await userEvent.type(ta, "  a small bunny ");
    await userEvent.click(screen.getByRole("button", { name: "產生角色" }));
    await waitFor(() => expect(screen.getAllByText(/排隊第 \d 位|生成中/)).toHaveLength(1));
    const posts = calls.filter((c) => c.url.endsWith("/character/candidates"));
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({ description: "a small bunny" });
    expect(screen.queryByText(/產生 4 張/)).not.toBeInTheDocument();
  });

  it("產生中（排隊或生成中）停用按鈕；完成後變成「再產生一張」，每按一次一個請求、一張候選，舊的保留", async () => {
    gens = [gen(1)];
    setup();
    const again = await screen.findByRole("button", { name: "再產生一張" });
    expect(screen.queryByRole("button", { name: "產生角色" })).not.toBeInTheDocument();
    await userEvent.click(again);
    await waitFor(() => expect(screen.getByRole("button", { name: "再產生一張" })).toBeDisabled());
    expect(screen.getByText("正在產生 1 張")).toBeInTheDocument();
    expect(screen.queryByText(/0 張排隊中/)).not.toBeInTheDocument();
    // 停用時連點不會再送
    await userEvent.click(screen.getByRole("button", { name: "再產生一張" }));
    expect(calls.filter((c) => c.url.endsWith("/character/candidates"))).toHaveLength(1);
    // 生成完成 → 按鈕恢復；兩張候選都在
    gens = gens.map((g) => ({ ...g, status: "succeeded", image_url: `/api/files/${g.id}.png`, queue_position: null }));
    await waitFor(() => expect(screen.getByRole("button", { name: "再產生一張" })).toBeEnabled(), { timeout: 4000 });
    expect(screen.getByRole("button", { name: "選 seed 101" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "選 seed 112" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "再產生一張" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/character/candidates"))).toHaveLength(2));
    expect(calls.filter((c) => c.url.endsWith("/character/candidates")).every((c) => !("count" in (c.body as object)))).toBe(true);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^選 seed/ })).toHaveLength(2));
    expect(screen.getAllByText(/排隊第 \d 位|生成中/)).toHaveLength(1);
  });

  it("沒有候選時的提示", async () => {
    setup();
    expect(await screen.findByText("還沒有候選圖。輸入描述後按「產生角色」。")).toBeInTheDocument();
  });

  it("候選都捨棄了：空白提示的按鈕名稱跟按鈕一致（「再產生一張」）", async () => {
    gens = [gen(1, { discarded: true })];
    setup();
    expect(await screen.findByText("還沒有候選圖。輸入描述後按「再產生一張」。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "再產生一張" })).toBeInTheDocument();
  });

  it("排隊提示：有生成中的才說「正在產生 1 張」，排隊 0 張時不說排隊", async () => {
    gens = [
      gen(1),
      gen(2, { status: "running", image_url: null, queue_position: 0 }),
      gen(3, { status: "queued", image_url: null, queue_position: 1 }),
    ];
    setup();
    expect(await screen.findByText("正在產生 1 張，1 張排隊中")).toBeInTheDocument();
  });

  it("排隊提示：都還在排隊時只說排隊張數", async () => {
    gens = [gen(1, { status: "queued", image_url: null, queue_position: 2 })];
    setup();
    expect(await screen.findByText("1 張排隊中")).toBeInTheDocument();
    expect(screen.queryByText(/正在產生/)).not.toBeInTheDocument();
  });

  it("描述提示：中英文都可以，不再要求英文；保留條紋、格子的建議", async () => {
    setup();
    await screen.findByRole("button", { name: "產生角色" });
    expect(screen.getByText(/中文或英文都可以。/)).toBeInTheDocument();
    expect(screen.getByText(/衣服避免條紋、格子等細碎圖樣/)).toBeInTheDocument();
    expect(screen.queryByText(/用英文描述/)).not.toBeInTheDocument();
  });

  it("描述空白時不能產生", async () => {
    setup();
    const btn = await screen.findByRole("button", { name: "產生角色" });
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText("描述角色的外型"), "   ");
    expect(btn).toBeDisabled();
  });

  it("以最新候選的描述預填", async () => {
    gens = [gen(1), gen(2)];
    setup();
    await waitFor(() => expect(screen.getByLabelText("描述角色的外型")).toHaveValue("bunny 2"));
  });

  it("選第二張再設定：帶該 id 並導到編輯器", async () => {
    gens = [gen(1), gen(2), gen(3)];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "選 seed 102" }));
    await userEvent.click(screen.getByRole("button", { name: /用這個角色，開始寫故事/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1/story"));
    expect(calls.find((c) => c.url === "/api/books/1/character")?.body).toEqual({ generation_id: 2 });
  });

  it("捨棄後預設隱藏，歷史切換可看見並還原", async () => {
    gens = [gen(1), gen(2)];
    setup();
    await userEvent.click(await screen.findByRole("button", { name: "捨棄 seed 102" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "選 seed 102" })).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /歷史 1/ }));
    expect(await screen.findByRole("button", { name: "選 seed 102" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "還原 seed 102" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").pop()?.body).toEqual({ discarded: false }));
  });

  it("失敗顯示錯誤訊息，排隊中可取消", async () => {
    gens = [gen(1, { status: "failed", image_url: null, error: "ComfyUI 逾時" }), gen(2, { status: "queued", image_url: null, queue_position: 1 })];
    setup();
    expect(await screen.findByText("ComfyUI 逾時")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "取消 seed 102" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/generations/2/cancel")).toBe(true));
  });

  it("線稿預覽切換使用 lineart-preview", async () => {
    gens = [gen(1)];
    setup();
    await userEvent.click(await screen.findByRole("tab", { name: "線稿預覽" }));
    const img = screen.getByAltText("角色線稿預覽");
    expect(img).toHaveAttribute("src", "/api/generations/1/lineart-preview");
  });

  it("CharacterStudio links to story after selection", async () => {
    gens = [gen(2)];
    bk = book({ character_generation_id: 2, character_description: "a small bunny" });
    setup();
    expect(await screen.findByRole("link", { name: "用這個角色，開始寫故事 →" })).toHaveAttribute("href", "/books/1/story");
  });

  it("已設定角色：唯讀，不顯示產生按鈕", async () => {
    gens = [gen(2)];
    bk = book({ character_generation_id: 2, character_description: "a small bunny" });
    setup();
    await screen.findByRole("link", { name: /開始寫故事/ });
    expect(screen.queryByRole("button", { name: /產生角色|再產生一張/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /用這個角色，開始寫故事/ })).not.toBeInTheDocument();
    expect(screen.getByText("a small bunny")).toBeInTheDocument();
    expect(screen.getByAltText("角色彩色圖")).toHaveAttribute("src", "/api/files/2.png");
  });

  it("後端 409 顯示中文訊息且不導頁", async () => {
    gens = [gen(1)];
    setCharacterResponse = () => j({ detail: "這本繪本已經設定過角色" }, 409);
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /用這個角色，開始寫故事/ }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("這本繪本已經設定過角色")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
