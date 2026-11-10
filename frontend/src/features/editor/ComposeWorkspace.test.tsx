import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation, Page } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { ComposeWorkspace } from "./ComposeWorkspace";

type O = Record<string, unknown>;
const mkPage = (o: O = {}): Page =>
  ({
    id: 1,
    book_id: 1,
    position: 0,
    scene_prompt: "舊書堆成小山",
    action_prompt: "抱著一本書往上爬",
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
  }) as Page;
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
  cutout_url: null,
  pose_mode: null,
  background_style: null,
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
let gate: Record<string, Promise<void> | undefined>;
let failWith: Record<string, number>;
let hasCharacter: boolean;

const j = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  gens = [];
  calls = [];
  gate = {};
  failWith = {};
  hasCharacter = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body =
        typeof init.body === "string" ? JSON.parse(init.body) : init.body instanceof FormData ? init.body : undefined;
      calls.push({ method, url, body });
      const g = gate[`${method} ${url}`];
      if (g) await g;
      const fail = failWith[`${method} ${url}`];
      if (fail) return j({ detail: "伺服器壞了" }, fail);
      if (url === "/api/books/1") return j({ id: 1, title: "書", character_generation_id: hasCharacter ? 99 : null });
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0, running_id: null });
      if (url.startsWith("/api/books/1/generations")) {
        const q = new URL(url, "http://x").searchParams;
        let list = gens.filter((x) => (!q.get("kind") || x.kind === q.get("kind")) && x.page_id === Number(q.get("page_id")));
        if (q.get("include_discarded") !== "true") list = list.filter((x) => !x.discarded);
        return j(list);
      }
      if (url === "/api/pages/1" && method === "PATCH") return j(mkPage(body as O));
      if (url === "/api/pages/1/backgrounds/upload") return j(mkPage({ background_generation_id: 70 }));
      const m = url.match(/^\/api\/pages\/1\/(backgrounds|poses)$/);
      if (m) {
        const made = gen(30 + gens.length, {
          kind: m[1] === "backgrounds" ? "background" : "pose",
          status: "queued",
          image_url: null,
          queue_position: 1,
        });
        gens = [...gens, made];
        return j(made, 202);
      }
      return j({ detail: "not found" }, 404);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function setup(
  o: { page?: Page; background?: Generation; pose?: Generation; picking?: boolean; beforeReplaceBackground?: () => Promise<void> } = {},
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onPickBackground = vi.fn();
  const onPickPose = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <ComposeWorkspace
          bookId={1}
          page={o.page ?? mkPage()}
          background={o.background}
          pose={o.pose}
          onPickBackground={onPickBackground}
          onPickPose={onPickPose}
          picking={o.picking}
          beforeReplaceBackground={o.beforeReplaceBackground}
        />
      </ToastProvider>
    </QueryClientProvider>,
  );
  return { onPickBackground, onPickPose };
}

const card = (name: string) => screen.getByRole("region", { name });
const calledWith = (method: string, url: string) => calls.filter((c) => c.method === method && c.url === url);

describe("ComposeWorkspace", () => {
  it("兩張卡片：背景與角色動作，各有縮圖與「換一張」", async () => {
    gens = [gen(3), gen(4, { kind: "pose" })];
    setup();
    expect(await within(card("背景")).findByRole("radio", { name: "背景第 1 張" })).toBeInTheDocument();
    expect(await within(card("角色動作")).findByRole("radio", { name: "角色動作第 1 張" })).toBeInTheDocument();
    expect(within(card("背景")).getByRole("button", { name: "換一張背景" })).toHaveTextContent("換一張");
    expect(within(card("角色動作")).getByRole("button", { name: "換一張角色動作" })).toHaveTextContent("換一張");
    // 新卡片沒有舊的切換與草圖
    expect(screen.queryByRole("radiogroup", { name: /背景風格|產生方式/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/構圖強度/)).not.toBeInTheDocument();
  });

  it("點縮圖呼叫 onPickBackground／onPickPose", async () => {
    gens = [gen(3), gen(4, { kind: "pose" })];
    const { onPickBackground, onPickPose } = setup();
    await userEvent.click(await screen.findByRole("radio", { name: "背景第 1 張" }));
    expect(onPickBackground).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
    await userEvent.click(await screen.findByRole("radio", { name: "角色動作第 1 張" }));
    expect(onPickPose).toHaveBeenCalledWith(expect.objectContaining({ id: 4 }));
  });

  it("卡片標題旁說明選用的是哪一張（AI 產生／上傳）", async () => {
    const bgs = [gen(3), gen(5, { source: "upload" })];
    gens = [...bgs, gen(4, { kind: "pose" })];
    setup({
      page: mkPage({ background_generation_id: 3, pose_generation_id: 4 }),
      background: bgs[0] as unknown as Generation,
      pose: gens[2] as unknown as Generation,
    });
    expect(await within(card("背景")).findByText("AI 產生 · 第 1 張")).toBeInTheDocument();
    expect(within(card("角色動作")).getByText("第 1 張")).toBeInTheDocument();
  });

  it("ComposeWorkspace upload calls useUploadBackground and shows the hint", async () => {
    setup();
    const bg = card("背景");
    expect(within(bg).getByText("線條簡單的插畫效果最好；照片擷取的線稿可能很亂，需要多補線。")).toBeInTheDocument();
    const input = within(bg).getByLabelText("上傳背景圖") as HTMLInputElement;
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("accept", "image/png,image/jpeg,image/webp");
    const file = new File(["png"], "my.png", { type: "image/png" });
    await userEvent.upload(input, file);
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds/upload")).toHaveLength(1));
    const form = calledWith("POST", "/api/pages/1/backgrounds/upload")[0].body as FormData;
    expect((form.get("file") as File).name).toBe("my.png");
  });

  it("上傳前先等 beforeReplaceBackground（呼叫端等修線寫完）；等待與上傳期間停用", async () => {
    let release!: () => void;
    const before = vi.fn(() => new Promise<void>((r) => (release = r)));
    setup({ beforeReplaceBackground: before });
    const input = within(card("背景")).getByLabelText("上傳背景圖");
    await userEvent.upload(input, new File(["png"], "a.png", { type: "image/png" }));
    expect(before).toHaveBeenCalledTimes(1);
    await act(async () => {});
    expect(calledWith("POST", "/api/pages/1/backgrounds/upload")).toHaveLength(0);
    expect(within(card("背景")).getByLabelText(/上傳/)).toBeDisabled();
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds/upload")).toHaveLength(1));
    await waitFor(() => expect(within(card("背景")).getByLabelText("上傳背景圖")).not.toBeDisabled());
  });

  it("選用進行中：「上傳背景圖」停用", () => {
    setup({ picking: true });
    expect(within(card("背景")).getByLabelText("上傳背景圖")).toBeDisabled();
  });

  it("上傳失敗 → toast", async () => {
    failWith["POST /api/pages/1/backgrounds/upload"] = 400;
    setup();
    await userEvent.upload(within(card("背景")).getByLabelText("上傳背景圖"), new File(["x"], "a.png", { type: "image/png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("伺服器壞了");
  });

  it("背景「換一張」送出 {style: simple}；改過的場景先存（PATCH）再產生", async () => {
    setup();
    const bg = card("背景");
    await userEvent.click(within(bg).getByRole("button", { name: "換一張背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/backgrounds")[0].body).toEqual({ style: "simple" });
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);

    // 場景編輯器收在 details 裡，摘要是目前的場景
    expect(within(bg).getByText("場景：舊書堆成小山")).toBeInTheDocument();
    const box = within(bg).getByLabelText("場景描述");
    await userEvent.clear(box);
    await userEvent.type(box, "下雨的森林");
    let release!: () => void;
    gate["PATCH /api/pages/1"] = new Promise<void>((r) => (release = r));
    await userEvent.click(within(bg).getByRole("button", { name: "換一張背景" }));
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ scene_prompt: "下雨的森林" });
    await act(async () => {});
    expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1);
    await act(async () => release());
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(2));
    // 只送一次（blur 已送出的不重送）
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1);
  });

  it("場景存檔失敗 → 不產生、toast", async () => {
    failWith["PATCH /api/pages/1"] = 500;
    setup();
    const bg = card("背景");
    await userEvent.type(within(bg).getByLabelText("場景描述"), "！");
    await userEvent.click(within(bg).getByRole("button", { name: "換一張背景" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("伺服器壞了");
    expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(0);
  });

  it("ComposeWorkspace 換一張 for pose uses page.action_prompt", async () => {
    gens = [gen(4, { kind: "pose", user_text: "別的動作" })];
    setup();
    const pose = card("角色動作");
    expect(within(pose).getByText("動作：抱著一本書往上爬")).toBeInTheDocument();
    await userEvent.click(within(pose).getByRole("button", { name: "換一張角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "抱著一本書往上爬", mode: "precise" });
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);
  });

  it("頁面沒有動作文字 → 用最新一張角色動作的 user_text；都沒有 → 停用", async () => {
    gens = [gen(4, { kind: "pose", user_text: "舊的" }), gen(6, { kind: "pose", user_text: "揮手" })];
    setup({ page: mkPage({ action_prompt: "" }) });
    const pose = card("角色動作");
    await waitFor(() => expect(within(pose).getByText("動作：揮手")).toBeInTheDocument());
    await userEvent.click(within(pose).getByRole("button", { name: "換一張角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "揮手", mode: "precise" });
  });

  it("沒有任何動作文字 → 「換一張」停用", async () => {
    setup({ page: mkPage({ action_prompt: "" }) });
    await waitFor(() => expect(calls.some((c) => c.url.includes("kind=pose"))).toBe(true));
    expect(within(card("角色動作")).getByRole("button", { name: "換一張角色動作" })).toBeDisabled();
  });

  it("改動作（常用動作片語）→ 先存 action_prompt 再產生", async () => {
    setup();
    const pose = card("角色動作");
    await userEvent.click(within(pose).getByRole("button", { name: "揮手" }));
    expect(within(pose).getByLabelText("動作描述")).toHaveValue("抱著一本書往上爬, waving one hand");
    await userEvent.click(within(pose).getByRole("button", { name: "換一張角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ action_prompt: "抱著一本書往上爬, waving one hand" });
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({
      action: "抱著一本書往上爬, waving one hand",
      mode: "precise",
    });
    const order = calls.map((c) => `${c.method} ${c.url}`);
    expect(order.indexOf("PATCH /api/pages/1")).toBeLessThan(order.indexOf("POST /api/pages/1/poses"));
  });

  it("離開欄位時存動作文字", async () => {
    setup();
    const box = within(card("角色動作")).getByLabelText("動作描述");
    await userEvent.type(box, "！");
    await userEvent.tab();
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ action_prompt: "抱著一本書往上爬！" });
  });

  it("書沒有角色：角色動作「換一張」停用並連到角色工作室", async () => {
    hasCharacter = false;
    setup();
    const pose = card("角色動作");
    expect(await within(pose).findByRole("link", { name: "先設定角色" })).toHaveAttribute("href", "/books/1/character");
    expect(within(pose).getByRole("button", { name: "換一張角色動作" })).toBeDisabled();
  });
});
