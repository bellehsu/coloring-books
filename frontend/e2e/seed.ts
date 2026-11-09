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

async function waitSucceeded(
  request: APIRequestContext,
  bookId: number,
  kind: "character" | "page",
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

/** 產生 count 張頁面候選，選第一張。 */
export async function generateAndSelect(
  request: APIRequestContext,
  bookId: number,
  pageId: number,
  count = 2,
): Promise<void> {
  const created = await ok<Generation[]>(await request.post(`/api/pages/${pageId}/candidates`, { data: { count } }));
  const gens = await waitSucceeded(request, bookId, "page", created.map((g) => g.id));
  await ok(await request.post(`/api/pages/${pageId}/select`, { data: { generation_id: gens[0].id } }));
}

/** 每次執行都不同的書名（本機重用伺服器時資料會累積）。 */
export const uniqueTitle = (prefix: string) => `${prefix} ${Date.now().toString(36)}`;
