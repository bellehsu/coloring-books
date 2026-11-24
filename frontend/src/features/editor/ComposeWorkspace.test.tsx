import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Generation, Page } from "@/api/client";
import { ToastProvider } from "@/components/ui/Toast";
import { ComposeWorkspace } from "./ComposeWorkspace";
import { alertToasts } from "@/components/ui/toastTesting";

type O = Record<string, unknown>;
const mkPage = (o: O = {}): Page =>
  ({
    id: 1,
    book_id: 1,
    position: 0,
    scene_prompt: "舊書堆成小山",
    action_prompt: "抱著一本書往上爬",
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
  user_text: "",
  width: 864,
  height: 1216,
  image_url: `/img/${id}.png`,
  cutout_url: null,
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
      if (url === "/api/system/queue") return j({ state: "idle", last_error: null, queued: 0 });
      if (url.startsWith("/api/books/1/generations")) {
        const q = new URL(url, "http://x").searchParams;
        let list = gens.filter((x) => (!q.get("kind") || x.kind === q.get("kind")) && x.page_id === Number(q.get("page_id")));
        if (q.get("include_discarded") !== "true") list = list.filter((x) => !x.discarded);
        return j(list);
      }
      if (url === "/api/pages/1" && method === "PATCH") return j(mkPage(body as O));
      if (url === "/api/pages/1/backgrounds/upload") return j(mkPage({ background_generation_id: 70 }));
      if (url === "/api/pages/1/backgrounds/cleanup") {
        const made = gen(30 + gens.length, { status: "queued", image_url: null, queue_position: 1, source_generation_id: 3 });
        gens = [...gens, made];
        return j(made, 202);
      }
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
  o: {
    page?: Page;
    background?: Generation;
    pose?: Generation;
    picking?: boolean;
    beforeReplaceBackground?: () => Promise<void>;
    compact?: boolean;
  } = {},
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
          compact={o.compact}
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
    await waitFor(() => expect(alertToasts()).toHaveTextContent("伺服器壞了"));
  });

  it("背景「換一張」送出空 body（不帶風格）；改過的場景先存（PATCH）再產生", async () => {
    setup();
    const bg = card("背景");
    await userEvent.click(within(bg).getByRole("button", { name: "換一張背景" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/backgrounds")[0].body).toEqual({});
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
    await waitFor(() => expect(alertToasts()).toHaveTextContent("伺服器壞了"));
    expect(calledWith("POST", "/api/pages/1/backgrounds")).toHaveLength(0);
  });

  it("ComposeWorkspace 換一張 for pose uses page.action_prompt", async () => {
    gens = [gen(4, { kind: "pose", user_text: "別的動作" })];
    setup();
    const pose = card("角色動作");
    expect(within(pose).getByText("動作：抱著一本書往上爬")).toBeInTheDocument();
    await userEvent.click(within(pose).getByRole("button", { name: "換一張角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "抱著一本書往上爬" });
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);
  });

  it("頁面沒有動作文字 → 用最新一張角色動作的 user_text；都沒有 → 停用", async () => {
    gens = [gen(4, { kind: "pose", user_text: "舊的" }), gen(6, { kind: "pose", user_text: "揮手" })];
    setup({ page: mkPage({ action_prompt: "" }) });
    const pose = card("角色動作");
    await waitFor(() => expect(within(pose).getByText("動作：揮手")).toBeInTheDocument());
    await userEvent.click(within(pose).getByRole("button", { name: "換一張角色動作" }));
    await waitFor(() => expect(calledWith("POST", "/api/pages/1/poses")).toHaveLength(1));
    expect(calledWith("POST", "/api/pages/1/poses")[0].body).toEqual({ action: "揮手" });
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
    });
    const order = calls.map((c) => `${c.method} ${c.url}`);
    expect(order.indexOf("PATCH /api/pages/1")).toBeLessThan(order.indexOf("POST /api/pages/1/poses"));
  });

  it("點晶片立即存 action_prompt（不用等 blur）：動作、表情、面向、取代表情", async () => {
    setup();
    const pose = card("角色動作");
    await userEvent.click(within(pose).getByRole("button", { name: "揮手" }));
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ action_prompt: "抱著一本書往上爬, waving one hand" });
    const expr = within(pose).getByRole("group", { name: "表情" });
    await userEvent.click(within(expr).getByRole("button", { name: "大笑" }));
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(2));
    expect(calledWith("PATCH", "/api/pages/1")[1].body).toEqual({
      action_prompt: "抱著一本書往上爬, waving one hand, with a big laughing expression",
    });
    await userEvent.click(within(expr).getByRole("button", { name: "難過" })); // 取代
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(3));
    expect(calledWith("PATCH", "/api/pages/1")[2].body).toEqual({
      action_prompt: "抱著一本書往上爬, waving one hand, with a sad expression",
    });
    await userEvent.click(within(pose).getByRole("group", { name: "面向" }).querySelector("button") as HTMLElement);
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(4));
    expect((calledWith("PATCH", "/api/pages/1")[3].body as { action_prompt: string }).action_prompt).toContain(
      "facing toward the left side of the picture",
    );
  });

  it("連續儲存依序送出：第一筆還沒回來，第二筆不送，最後落地的是新的文字", async () => {
    setup();
    let release!: () => void;
    gate["PATCH /api/pages/1"] = new Promise<void>((r) => (release = r));
    const pose = card("角色動作");
    const box = within(pose).getByLabelText("動作描述");
    await userEvent.clear(box);
    await userEvent.type(box, "X");
    await userEvent.click(within(pose).getByRole("button", { name: "揮手" })); // blur 送 "X"，點擊送 "X, waving one hand"
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1);
    release();
    await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(2));
    const bodies = calledWith("PATCH", "/api/pages/1").map((c) => c.body);
    expect(bodies[0]).toEqual({ action_prompt: "X" });
    expect(bodies[1]).toEqual({ action_prompt: "X, waving one hand" });
  });

  it("表情與面向：單選、再按一次取消、超過長度停用、面向提示", async () => {
    setup();
    const pose = card("角色動作");
    const box = within(pose).getByLabelText("動作描述");
    const expr = within(pose).getByRole("group", { name: "表情" });
    const facing = within(pose).getByRole("group", { name: "面向" });
    expect(within(pose).getByText("臉會轉向，身體可能仍偏正面；不夠時可在構圖用左右翻轉")).toBeInTheDocument();
    await userEvent.click(within(expr).getByRole("button", { name: "開心" }));
    await userEvent.click(within(expr).getByRole("button", { name: "大笑" }));
    expect(box).toHaveValue("抱著一本書往上爬, with a big laughing expression");
    expect(within(expr).getByRole("button", { name: "大笑" })).toHaveAttribute("aria-pressed", "true");
    expect(within(expr).getByRole("button", { name: "開心" })).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(within(facing).getByRole("button", { name: "朝左" }));
    expect(box).toHaveValue("抱著一本書往上爬, with a big laughing expression, facing toward the left side of the picture");
    await userEvent.click(within(expr).getByRole("button", { name: "大笑" }));
    expect(box).toHaveValue("抱著一本書往上爬, facing toward the left side of the picture");
    await userEvent.click(within(facing).getByRole("button", { name: "朝右" }));
    expect(box).toHaveValue("抱著一本書往上爬, facing toward the right side of the picture");
  });

  it("表情：加入會超過 300 字的晶片停用，但作用中的晶片仍可取消", async () => {
    setup({ page: mkPage({ action_prompt: "x".repeat(270) }) });
    const pose = card("角色動作");
    const expr = within(pose).getByRole("group", { name: "表情" });
    expect(within(expr).getByRole("button", { name: "大笑" })).toBeDisabled();
  });

  it("表情：接近 300 字時，作用中的晶片仍可按下取消", async () => {
    const base = "x".repeat(260);
    setup({ page: mkPage({ action_prompt: `${base}, with a sad expression` }) });
    const pose = card("角色動作");
    const expr = within(pose).getByRole("group", { name: "表情" });
    const sad = within(expr).getByRole("button", { name: "難過" });
    expect(sad).toHaveAttribute("aria-pressed", "true");
    expect(sad).toBeEnabled();
    await userEvent.click(sad);
    expect(within(pose).getByLabelText("動作描述")).toHaveValue(base);
  });

  it("動作描述提示：地點寫在背景的場景描述，用 aria-describedby 連到動作欄位", () => {
    setup();
    const pose = card("角色動作");
    const hint = "只寫動作，地點寫在背景的場景描述（例如：寫「坐著看書」，不要寫「坐在草地上看書」）";
    expect(within(pose).getByText(hint)).toBeInTheDocument();
    const box = within(pose).getByLabelText("動作描述");
    expect(box).toHaveAccessibleDescription(expect.stringContaining(hint));
    expect(box).toHaveAccessibleDescription(expect.stringMatching(/\d+ \/ 300/));
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
    expect(await within(pose).findByRole("link", { name: "先設定角色" })).toHaveAttribute("href", "/books/1/setup");
    expect(within(pose).getByRole("button", { name: "換一張角色動作" })).toBeDisabled();
  });

  describe("compact（手機寬度）", () => {
    it("先收成精簡卡片：選用的縮圖＋標題，沒有展開的內容", () => {
      const bg = gen(3);
      const pose = gen(4, { kind: "pose", cutout_url: "/cut/4.png" });
      gens = [bg, pose];
      setup({
        compact: true,
        page: mkPage({ background_generation_id: 3, pose_generation_id: 4 }),
        background: bg as unknown as Generation,
        pose: pose as unknown as Generation,
      });
      const bgCard = screen.getByRole("button", { name: /^背景/ });
      const poseCard = screen.getByRole("button", { name: /^角色動作/ });
      expect(bgCard).toHaveAttribute("aria-expanded", "false");
      expect(bgCard).toHaveTextContent("換一張・上傳");
      expect(bgCard.querySelector("img")).toHaveAttribute("src", "/img/3.png");
      expect(poseCard.querySelector("img")).toHaveAttribute("src", "/cut/4.png");
      // 背景與角色動作的展開內容都沒有（圖下方的文字卡片手機也直接顯示）
      expect(screen.queryByRole("region", { name: /^(背景|角色動作)$/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "換一張背景" })).not.toBeInTheDocument();
    });

    it("點了展開完整卡片，「收合」收回；收合不會丟掉改到一半的文字", async () => {
      setup({ compact: true });
      await userEvent.click(screen.getByRole("button", { name: /^角色動作/ }));
      const pose = card("角色動作");
      expect(within(pose).getByRole("button", { name: "換一張角色動作" })).toBeInTheDocument();
      // 按下的按鈕換掉了：焦點移到「收合」
      expect(within(pose).getByRole("button", { name: "收合角色動作" })).toHaveFocus();
      // 另一張還是收合的
      expect(screen.getByRole("button", { name: /^背景/ })).toHaveAttribute("aria-expanded", "false");
      const box = within(pose).getByLabelText("動作描述");
      await userEvent.clear(box);
      await userEvent.type(box, "跳");
      await userEvent.click(within(pose).getByRole("button", { name: "收合角色動作" }));
      expect(screen.queryByRole("region", { name: "角色動作" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^角色動作/ })).toHaveFocus();
      await userEvent.click(screen.getByRole("button", { name: /^角色動作/ }));
      expect(within(card("角色動作")).getByLabelText("動作描述")).toHaveValue("跳");
    });

    it("桌機（不是 compact）沒有精簡卡片與「收合」", () => {
      setup();
      expect(screen.queryByRole("button", { expanded: false })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^收合/ })).not.toBeInTheDocument();
    });
  });
  describe("圖下方的文字卡片", () => {
    it("有中文字、英文字兩個欄位與剩餘字數；離開欄位時 PATCH，清空送空字串", async () => {
      setup({ page: mkPage({ caption_zh: "消防車", caption_en: null }) });
      const cap = card("圖下方的文字");
      const zh = within(cap).getByRole("textbox", { name: "中文字" });
      const en = within(cap).getByRole("textbox", { name: "英文字" });
      expect(zh).toHaveValue("消防車");
      expect(within(cap).getByText("還可 9 字")).toBeInTheDocument();
      expect(within(cap).getByText("還可 28 字元")).toBeInTheDocument();
      await userEvent.type(en, "Fire Engine");
      expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(0);
      await userEvent.tab();
      await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
      expect(calledWith("PATCH", "/api/pages/1")[0].body).toEqual({ caption_en: "Fire Engine" });
      await userEvent.clear(zh);
      await userEvent.tab();
      await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(2));
      expect(calledWith("PATCH", "/api/pages/1")[1].body).toEqual({ caption_zh: "" });
    });

    it("超過上限：顯示規格訊息、不送出；𠮷 12 個剛好可以", async () => {
      setup();
      const cap = card("圖下方的文字");
      const zh = within(cap).getByRole("textbox", { name: "中文字" });
      await userEvent.type(zh, "𠮷".repeat(12));
      await userEvent.tab();
      await waitFor(() => expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1));
      expect(within(cap).queryByText("中文字最多 12 個字")).not.toBeInTheDocument();
      await userEvent.type(zh, "字");
      await userEvent.tab();
      expect(within(cap).getByText("中文字最多 12 個字")).toBeInTheDocument();
      const en = within(cap).getByRole("textbox", { name: "英文字" });
      await userEvent.type(en, "x".repeat(29));
      await userEvent.tab();
      expect(within(cap).getByText("英文字最多 28 個字元")).toBeInTheDocument();
      await act(async () => {});
      expect(calledWith("PATCH", "/api/pages/1")).toHaveLength(1);
    });

    it("C 關掉的那行：欄位下方說「這行目前不印（在處理頁面可打開）」；兩行都關時不說會印", () => {
      setup({ page: mkPage({ caption_zh: "消防車", caption_en: "Fire Engine", caption_show_zh: true, caption_show_en: false }) });
      const cap = card("圖下方的文字");
      expect(within(cap).getAllByText("這行目前不印（在處理頁面可打開）")).toHaveLength(1);
      const enField = within(cap).getByRole("textbox", { name: "英文字" }).closest("div")!.parentElement!;
      expect(enField).toHaveTextContent("這行目前不印（在處理頁面可打開）");
      expect(within(cap).getByText("選填，印在列印頁的圖下方。")).toBeInTheDocument();
    });

    it("兩行都關：兩行都說目前不印，「印在列印頁的圖下方」的提示不出現", () => {
      setup({ page: mkPage({ caption_show_zh: false, caption_show_en: false }) });
      const cap = card("圖下方的文字");
      expect(within(cap).getAllByText("這行目前不印（在處理頁面可打開）")).toHaveLength(2);
      expect(within(cap).queryByText("選填，印在列印頁的圖下方。")).not.toBeInTheDocument();
    });

    it("兩行都開：沒有不印的說明", () => {
      setup({ page: mkPage({ caption_show_zh: true, caption_show_en: true }) });
      expect(within(card("圖下方的文字")).queryByText("這行目前不印（在處理頁面可打開）")).not.toBeInTheDocument();
    });

    it("手機（compact）也看得到，不用先展開", () => {
      setup({ compact: true });
      expect(within(card("圖下方的文字")).getByRole("textbox", { name: "中文字" })).toBeInTheDocument();
    });
  });

  describe("清掉框外雜物", () => {
    const PORTRAIT = { cx: 0.5, cy: 0.59, height: 0.42, flip: false, frame: { x: 0.5, y: 0.55 }, autofit: true };
    const portraitPage = (o: O = {}) => mkPage({ background_generation_id: 3, placement: PORTRAIT, ...o });
    const cleanBtn = () => within(card("背景")).getByRole("button", { name: "清掉框外雜物" });

    it("肖像頁有選用的背景：按下排一筆清理（不帶 oneclick），排好之後停用", async () => {
      gens = [gen(3)];
      setup({ page: portraitPage(), background: gens[0] as unknown as Generation });
      await within(card("背景")).findByRole("radio", { name: "背景第 1 張" });
      expect(cleanBtn()).toBeEnabled();
      expect(within(card("背景")).queryByText("有框的背景（肖像模式）才能清")).toBeNull();
      await userEvent.click(cleanBtn());
      await waitFor(() => expect(calledWith("POST", "/api/pages/1/backgrounds/cleanup")).toHaveLength(1));
      // 清理排進去了（縮圖列有排隊中的一張）：按鈕停用
      await waitFor(() => expect(cleanBtn()).toBeDisabled());
    });

    it("沒有選用的背景 → 停用", async () => {
      setup({ page: portraitPage({ background_generation_id: null }) });
      expect(cleanBtn()).toBeDisabled();
    });

    it("這頁的背景正在產生（或清理）→ 停用", async () => {
      gens = [gen(3), gen(4, { status: "running", image_url: null })];
      setup({ page: portraitPage(), background: gens[0] as unknown as Generation });
      await within(card("背景")).findByRole("radio", { name: "背景第 1 張" });
      await waitFor(() => expect(cleanBtn()).toBeDisabled());
    });

    it("不是肖像模式（位置沒有 frame）→ 停用，旁邊說明", async () => {
      gens = [gen(3)];
      setup({ page: mkPage({ background_generation_id: 3, placement: { ...PORTRAIT, frame: null } }), background: gens[0] as unknown as Generation });
      expect(cleanBtn()).toBeDisabled();
      expect(within(card("背景")).getByText("有框的背景（肖像模式）才能清")).toBeInTheDocument();
    });

    it("後端拒絕（409／429）→ toast 後端的訊息", async () => {
      gens = [gen(3)];
      failWith["POST /api/pages/1/backgrounds/cleanup"] = 429;
      setup({ page: portraitPage(), background: gens[0] as unknown as Generation });
      await within(card("背景")).findByRole("radio", { name: "背景第 1 張" });
      await userEvent.click(cleanBtn());
      await waitFor(() => expect(alertToasts()).toHaveTextContent("伺服器壞了"));
    });
  });
});
