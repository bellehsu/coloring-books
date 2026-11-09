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
  prompt: `single full body front view of bunny ${id}, standing, children's picture book illustration`,
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
        gens = [
          ...gens,
          ...[11, 12, 13, 14].map((id, i) => gen(id, { status: "queued", image_url: null, queue_position: i })),
        ];
        return j(gens.slice(-4), 201);
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
  it("產生 4 張：送出描述與 count，出現 4 張排隊卡", async () => {
    setup();
    const ta = await screen.findByLabelText("描述角色的外型");
    await userEvent.type(ta, "  a small bunny ");
    await userEvent.click(screen.getByRole("button", { name: "產生 4 張" }));
    await waitFor(() => expect(screen.getAllByText(/排隊第 \d 位|生成中/)).toHaveLength(4));
    expect(screen.getByText("生成中")).toBeInTheDocument();
    expect(screen.getByText("排隊第 2 位")).toBeInTheDocument();
    const post = calls.find((c) => c.url.endsWith("/character/candidates"));
    expect(post?.body).toEqual({ description: "a small bunny", count: 4 });
  });

  it("描述空白時不能產生", async () => {
    setup();
    const btn = await screen.findByRole("button", { name: "產生 4 張" });
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
    await userEvent.click(screen.getByRole("button", { name: /設為本書角色，開始做頁面/ }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/books/1/editor"));
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

  it("已設定角色：唯讀，不顯示產生按鈕", async () => {
    gens = [gen(2)];
    bk = book({ character_generation_id: 2, character_description: "a small bunny" });
    setup();
    const link = await screen.findByRole("link", { name: /前往頁面編輯器/ });
    expect(link).toHaveAttribute("href", "/books/1/editor");
    expect(screen.queryByRole("button", { name: "產生 4 張" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /設為本書角色/ })).not.toBeInTheDocument();
    expect(screen.getByText("a small bunny")).toBeInTheDocument();
    expect(screen.getByAltText("角色彩色圖")).toHaveAttribute("src", "/api/files/2.png");
  });

  it("後端 409 顯示中文訊息且不導頁", async () => {
    gens = [gen(1)];
    setCharacterResponse = () => j({ detail: "這本繪本已經設定過角色" }, 409);
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /設為本書角色/ }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("這本繪本已經設定過角色")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
