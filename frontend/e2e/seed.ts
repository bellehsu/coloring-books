import { crc32, deflateSync } from "node:zlib";
import { expect, type APIRequestContext } from "@playwright/test";

/** 經 Next 的 /api 代理直接呼叫（假）後端，準備測試資料。 */

interface Book {
  id: number;
  title: string;
}
interface Page {
  id: number;
}
interface Generation {
  id: number;
  status: string;
}

async function ok<T>(res: Awaited<ReturnType<APIRequestContext["get"]>>): Promise<T> {
  expect(res.ok(), `${res.url()} → ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as T;
}

export async function createBook(request: APIRequestContext, title: string): Promise<Book> {
  return ok<Book>(await request.post("/api/books", { data: { title, paper_size: "a4", print_mode: "print_shop" } }));
}

export async function createPage(request: APIRequestContext, bookId: number, scene = ""): Promise<Page> {
  return ok<Page>(await request.post(`/api/books/${bookId}/pages`, { data: { scene_prompt: scene } }));
}

type Kind = "character" | "background" | "pose";

async function waitSucceeded(
  request: APIRequestContext,
  bookId: number,
  kind: Kind,
  ids: number[],
): Promise<Generation[]> {
  let gens: Generation[] = [];
  await expect
    .poll(
      async () => {
        const all = await ok<Generation[]>(await request.get(`/api/books/${bookId}/generations?kind=${kind}`));
        gens = all.filter((g) => ids.includes(g.id));
        return gens.length === ids.length && gens.every((g) => g.status === "succeeded");
      },
      { timeout: 20_000 },
    )
    .toBe(true);
  return gens;
}

/** 產生 1 張角色候選並設為本書角色。 */
export async function setCharacter(request: APIRequestContext, bookId: number): Promise<void> {
  const created = await ok<Generation[]>(
    await request.post(`/api/books/${bookId}/character/candidates`, { data: { description: "a round cat", count: 1 } }),
  );
  const [gen] = await waitSucceeded(request, bookId, "character", created.map((g) => g.id));
  await ok(await request.post(`/api/books/${bookId}/character`, { data: { generation_id: gen.id } }));
}

/**
 * 合成流程：產生並選用 1 張背景、1 個角色動作（需已設定角色；產生方式 poseMode，預設精準），
 * 存一個位置草稿；confirm 時再確認合成。
 */
export async function composePage(
  request: APIRequestContext,
  bookId: number,
  pageId: number,
  {
    confirm = false,
    placement = { cx: 0.45, cy: 0.66, height: 0.6, flip: false },
    poseMode = "precise",
  }: {
    confirm?: boolean;
    placement?: { cx: number; cy: number; height: number; flip: boolean };
    poseMode?: "precise" | "fast";
  } = {},
): Promise<void> {
  const bg = await ok<Generation>(await request.post(`/api/pages/${pageId}/backgrounds`));
  const pose = await ok<Generation>(
    await request.post(`/api/pages/${pageId}/poses`, { data: { action: "waving one hand", mode: poseMode } }),
  );
  await waitSucceeded(request, bookId, "background", [bg.id]);
  await waitSucceeded(request, bookId, "pose", [pose.id]);
  await ok(await request.post(`/api/pages/${pageId}/background`, { data: { generation_id: bg.id } }));
  await ok(await request.post(`/api/pages/${pageId}/pose`, { data: { generation_id: pose.id } }));
  await ok(await request.put(`/api/pages/${pageId}/placement`, { data: placement }));
  if (confirm) await ok(await request.post(`/api/pages/${pageId}/compose`));
}

/** 白底、中間一條黑色橫線的灰階 PNG（size×size）：當成構圖草圖上傳。 */
function sketchPng(size = 256): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // 8-bit
  ihdr[9] = 0; // 灰階
  const rows: Buffer[] = [];
  for (let y = 0; y < size; y++) {
    const row = Buffer.alloc(size + 1, 255);
    row[0] = 0; // filter: none
    if (Math.abs(y - size / 2) < 3) row.fill(0, 1 + size / 4, 1 + (size * 3) / 4);
    rows.push(row);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(Buffer.concat(rows))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** 上傳一張構圖草圖（PUT /pages/{id}/sketch）。 */
export async function uploadSketch(request: APIRequestContext, pageId: number): Promise<void> {
  await ok(
    await request.put(`/api/pages/${pageId}/sketch`, {
      multipart: { file: { name: "sketch.png", mimeType: "image/png", buffer: sketchPng() } },
    }),
  );
}

/** 每次執行都不同的書名（本機重用伺服器時資料會累積）。 */
export const uniqueTitle = (prefix: string) => `${prefix} ${Date.now().toString(36)}`;
