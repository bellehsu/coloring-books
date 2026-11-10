import { crc32, deflateSync } from "node:zlib";
import { expect, type APIRequestContext } from "@playwright/test";

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
 * 產生 1 張背景、1 個角色動作（需已設定角色）。成功後 worker 自動選用（欄位是空的才選），
 * 這裡等兩個欄位都選好；有給 placement 就存位置；compose 時再合成（頁面有選定的合成圖）。
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
  if (placement) await ok(await request.put(`/api/pages/${pageId}/placement`, { data: placement }));
  if (compose) await ok(await request.post(`/api/pages/${pageId}/compose`));
}

/**
 * width×height 的 8-bit 灰階 PNG：淺灰底，x＝10%、50%、90% 各一條 8 px 黑色直線。
 * 當成上傳的背景圖（寬圖裁成頁面比例後只剩中間那條）。
 */
export function grayPng(width: number, height: number): Buffer {
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
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // 8-bit
  ihdr[9] = 0; // 灰階
  const row = Buffer.alloc(width + 1, 230);
  row[0] = 0; // filter: none
  for (const f of [0.1, 0.5, 0.9]) {
    const x = Math.round(width * f);
    row.fill(0, 1 + x - 4, 1 + x + 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(Buffer.concat(Array.from({ length: height }, () => row)))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
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

/** 每次執行都不同的書名（本機重用伺服器時資料會累積）。 */
export const uniqueTitle = (prefix: string) => `${prefix} ${Date.now().toString(36)}`;
