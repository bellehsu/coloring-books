import { crc32, deflateSync } from "node:zlib";
import { expect, request as pwRequest, test, type APIRequestContext } from "@playwright/test";

/** 經 Next 的 /api 代理直接呼叫（假）後端，準備測試資料。 */

interface Book {
  id: number;
  title: string;
}
export interface Page {
  id: number;
  background_generation_id: number | null;
  pose_generation_id: number | null;
  selected_generation_id: number | null;
  status: string;
}
export interface Generation {
  id: number;
  status: string;
  page_id: number | null;
  user_text: string | null;
  source: string | null;
}

export async function ok<T>(res: Awaited<ReturnType<APIRequestContext["get"]>>): Promise<T> {
  expect(res.ok(), `${res.url()} → ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function createBook(request: APIRequestContext, title: string): Promise<Book> {
  return ok<Book>(await request.post("/api/books", { data: { title, paper_size: "a4", print_mode: "print_shop" } }));
}

export async function createPage(request: APIRequestContext, bookId: number, scene = "", action = ""): Promise<Page> {
  return ok<Page>(
    await request.post(`/api/books/${bookId}/pages`, { data: { scene_prompt: scene, action_prompt: action } }),
  );
}

export async function getPages(request: APIRequestContext, bookId: number): Promise<Page[]> {
  return ok<Page[]>(await request.get(`/api/books/${bookId}/pages`));
}

type Kind = "character" | "background" | "pose";

export async function getGenerations(request: APIRequestContext, bookId: number, kind: Kind): Promise<Generation[]> {
  return ok<Generation[]>(await request.get(`/api/books/${bookId}/generations?kind=${kind}`));
}

async function waitSucceeded(request: APIRequestContext, bookId: number, kind: Kind, ids: number[]): Promise<void> {
  await expect
    .poll(
      async () => {
        const gens = (await getGenerations(request, bookId, kind)).filter((g) => ids.includes(g.id));
        return gens.length === ids.length && gens.every((g) => g.status === "succeeded");
      },
      { timeout: 20_000 },
    )
    .toBe(true);
}

/** 產生 1 張角色（一次一張）並設為本書角色。 */
export async function setCharacter(request: APIRequestContext, bookId: number): Promise<void> {
  const [gen] = await ok<Generation[]>(
    await request.post(`/api/books/${bookId}/character/candidates`, { data: { description: "一隻圓圓的小貓" } }),
  );
  await waitSucceeded(request, bookId, "character", [gen.id]);
  await ok(await request.post(`/api/books/${bookId}/character`, { data: { generation_id: gen.id } }));
}

/**
 * 產生 1 張背景、1 個角色動作（需已設定角色）。成功後 worker 自動選用，頁面流程（Phase 13）接著自動合成、
 * 補線並標成可以印；這裡等流程做完（不和它同時改這一頁），有給 placement 就存位置；compose 時再合成。
 */
export async function composePage(
  request: APIRequestContext,
  bookId: number,
  pageId: number,
  {
    compose = false,
    placement,
  }: {
    compose?: boolean;
    placement?: { cx: number; cy: number; height: number; flip: boolean };
  } = {},
): Promise<void> {
  const bg = await ok<Generation>(await request.post(`/api/pages/${pageId}/backgrounds`));
  const pose = await ok<Generation>(await request.post(`/api/pages/${pageId}/poses`, { data: { action: "揮手" } }));
  await waitSucceeded(request, bookId, "background", [bg.id]);
  await waitSucceeded(request, bookId, "pose", [pose.id]);
  await expect
    .poll(async () => {
      const p = (await getPages(request, bookId)).find((x) => x.id === pageId);
      return [p?.background_generation_id, p?.pose_generation_id];
    })
    .toEqual([bg.id, pose.id]);
  await expect
    .poll(async () => (await getPages(request, bookId)).find((x) => x.id === pageId)?.status, { timeout: 20_000 })
    .toBe("passed");
  if (placement) await ok(await request.put(`/api/pages/${pageId}/placement`, { data: placement }));
  if (compose) await ok(await request.post(`/api/pages/${pageId}/compose`));
}

/** 8-bit 灰階 PNG 編碼：rows[y] 是第 y 列的像素（0＝黑、255＝白）。 */
function encodeGrayPng(width: number, rows: Buffer[]): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(rows.length, 4);
  ihdr[8] = 8; // 8-bit
  ihdr[9] = 0; // 灰階
  // 每列前面一個 filter byte（0＝none）
  const raw = Buffer.concat(rows.map((r) => Buffer.concat([Buffer.from([0]), r])));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/**
 * width×height 的 8-bit 灰階 PNG：淺灰底，x＝10%、50%、90% 各一條 8 px 黑色直線。
 * 當成上傳的背景圖（寬圖裁成頁面比例後只剩中間那條）。
 */
export function grayPng(width: number, height: number): Buffer {
  const row = Buffer.alloc(width, 230);
  for (const f of [0.1, 0.5, 0.9]) {
    const x = Math.round(width * f);
    row.fill(0, x - 4, x + 4);
  }
  return encodeGrayPng(width, Array.from({ length: height }, () => row));
}

/**
 * width×height 的「相框」背景：白底，一個 16 px 黑色長方形框（x 15%–85%、y 12%–90%），框內是空的。
 * 預設種子點 (0.5, 0.55) 落在框裡，後端找得到內圈（放進框裡）。
 */
export function framePng(width: number, height: number): Buffer {
  const t = 16;
  const [x0, x1] = [Math.round(width * 0.15), Math.round(width * 0.85)];
  const [y0, y1] = [Math.round(height * 0.12), Math.round(height * 0.9)];
  const plain = Buffer.alloc(width, 250);
  const edge = Buffer.alloc(width, 250).fill(0, x0, x1);
  const sides = Buffer.alloc(width, 250).fill(0, x0, x0 + t).fill(0, x1 - t, x1);
  return encodeGrayPng(
    width,
    Array.from({ length: height }, (_, y) =>
      y < y0 || y >= y1 ? plain : y < y0 + t || y >= y1 - t ? edge : sides,
    ),
  );
}

/** 上傳一張背景圖（POST /pages/{id}/backgrounds/upload），回傳更新後的頁。 */
export async function uploadBackground(
  request: APIRequestContext,
  pageId: number,
  png: Buffer = grayPng(1200, 1600),
): Promise<Page> {
  return ok<Page>(
    await request.post(`/api/pages/${pageId}/backgrounds/upload`, {
      multipart: { file: { name: "bg.png", mimeType: "image/png", buffer: png } },
    }),
  );
}

/**
 * 所有 spec 共用一個假後端、一條全域生成佇列：會排生成的 spec 結束（afterAll）時呼叫，
 * 等排隊中與執行中的任務都做完（輪詢 GET /api/system/queue），下一個 spec 才不會被前面留下的任務拖慢。
 */
export async function drainQueue(request: APIRequestContext, timeoutMs = 60_000): Promise<void> {
  await expect
    .poll(
      async () => {
        const q = await ok<{ state: string; queued: number; pending: unknown[] }>(await request.get("/api/system/queue"));
        return q.queued === 0 && q.pending.length === 0;
      },
      { timeout: timeoutMs, message: "生成佇列沒有在時限內清空" },
    )
    .toBe(true);
}

/**
 * 在 spec 檔最上層呼叫一次：每個測試結束（afterEach，同一檔案裡下一個測試不會被前一個留下的任務拖慢，
 * 例如 oneclick 的「開始做這本書」排了整本）與整個檔案結束（afterAll）都清空佇列。
 */
export function drainQueueAfterAll(): void {
  test.afterEach(async ({ request }) => {
    test.setTimeout(test.info().timeout + 60_000);
    await drainQueue(request);
  });
  test.afterAll(async () => {
    test.setTimeout(90_000);
    const ctx = await pwRequest.newContext({ baseURL: test.info().project.use.baseURL });
    try {
      await drainQueue(ctx);
    } finally {
      await ctx.dispose();
    }
  });
}

/** 每次執行都不同的書名（本機重用伺服器時資料會累積）。 */
export const uniqueTitle = (prefix: string) => `${prefix} ${Date.now().toString(36)}`;
