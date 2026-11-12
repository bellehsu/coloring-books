import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { composePage, createBook, createPage, getPages, setCharacter, uniqueTitle } from "./seed";

/**
 * 進階編輯和 C 同時開著同一頁（Review Focus 1、E1／E3）：在 C 拖角色 → 頁面流程排隊重新合成；
 * 進階編輯在流程做完前停用 ② 補線並說明，做完後自己更新（每 2 秒重抓）、舞台換成 C 的新位置。
 * 先替其他幾頁排好重新合成，這頁的流程排在它們後面，進階編輯才看得到「正在重新合成」。
 * 假後端（DemoEngine）產生圖，worker 的頁面流程重新合成。
 */
const NOTICE = "這一頁正在重新合成…好了會自動更新";
const FILLERS = 2;

let bookId = 0;
let target = 0;
let fillers: number[] = [];

interface PageOut {
  id: number;
  status: string;
  recompose_pending: boolean;
  placement: { cx: number; cy: number; height: number; flip: boolean } | null;
}

test.beforeAll(async ({ playwright }, testInfo) => {
  test.setTimeout(180_000);
  const request = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  const book = await createBook(request, uniqueTitle("e2e 進階編輯與C"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "陽光公園，有一棵大樹", "開心地揮手");
  await composePage(request, book.id, p.id);
  const others: number[] = [];
  for (let i = 0; i < FILLERS; i++) {
    const f = await createPage(request, book.id, `安靜的海邊 ${i}`, "坐著看書");
    await composePage(request, book.id, f.id);
    others.push(f.id);
  }
  bookId = book.id;
  target = p.id;
  fillers = others;
  await request.dispose();
});

async function pageOut(request: APIRequestContext, id: number): Promise<PageOut> {
  return (await getPages(request, bookId)).find((p) => p.id === id) as unknown as PageOut;
}

/** 角色框的位置（左上角，背景的百分比）：C 與進階編輯用同一個舞台元件，同樣的位置就是同樣的值。 */
async function boxLeft(page: Page): Promise<string> {
  const character = page.getByRole("group", { name: "角色位置" });
  await expect(character).toHaveAttribute("data-ready", "true");
  return character.evaluate((el) => (el as HTMLElement).style.left);
}

test("C 拖角色時進階編輯停用補線、說明正在重新合成；做好了自己更新成 C 的新位置", async ({ page, context, request }) => {
  test.setTimeout(120_000);

  // 進階編輯（第二個分頁）：先打開一次（開發模式第一次要編譯），補線可用
  const editor = await context.newPage();
  await editor.goto(`/books/${bookId}/editor?page=${target}`);
  const lineTab = editor.getByRole("tab", { name: /^② 補線/ });
  await expect(lineTab).not.toHaveAttribute("aria-disabled", "true");
  const before = await boxLeft(editor);

  // C（第一個分頁）：彩色原圖，角色可以拖
  await page.goto(`/books/${bookId}/page/${target}`);
  await page.getByRole("radio", { name: "彩色原圖" }).click();
  const character = page.getByRole("group", { name: "角色位置" });
  await expect(character).toHaveAttribute("data-ready", "true");

  // 其他頁先排重新合成（位置每次都不同，沒變的位置不會重新合成），這頁的流程排在後面
  const jitter = Math.random() * 0.05;
  for (const [i, f] of fillers.entries()) {
    const res = await request.put(`/api/pages/${f}/placement?oneclick=true`, {
      data: { cx: 0.4 + 0.02 * i + jitter, cy: 0.62, height: 0.5, flip: i % 2 === 0 },
    });
    expect(res.ok()).toBeTruthy();
  }

  // C 把角色往左拖
  const cb = (await character.boundingBox())!;
  const ov = (await page.getByTestId("compose-overlay").boundingBox())!;
  const placed = page.waitForResponse((r) => /\/placement\?oneclick=true$/.test(r.url()) && r.request().method() === "PUT");
  await page.mouse.move(cb.x + cb.width / 2, cb.y + cb.height / 2);
  await page.mouse.down();
  await page.mouse.move(cb.x + cb.width / 2 - ov.width * 0.05, cb.y + cb.height / 2, { steps: 4 });
  await page.mouse.move(cb.x + cb.width / 2 - ov.width * 0.1, cb.y + cb.height / 2, { steps: 4 });
  await page.mouse.up();
  const after = (await (await placed).json()) as PageOut;
  expect(after.recompose_pending).toBe(true);
  const cLeft = await boxLeft(page);
  expect(cLeft).not.toBe(before);
  const composes: string[] = [];
  editor.on("request", (r) => {
    if (/\/compose$/.test(r.url()) && r.method() === "POST") composes.push(r.url());
  });

  // 切回開著的進階編輯（不重新整理）：分頁變回可見／取得 focus 就重抓 → 這頁還在重新合成 → 補線停用、說明；
  // 位置已經是 C 的新位置（伺服器的，不是舊的快取）。headless 不一定送出這兩個事件，自己補上
  await editor.bringToFront();
  await editor.evaluate(() => {
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
  });
  await expect(editor.getByRole("status").filter({ hasText: NOTICE })).toBeVisible();
  await expect(lineTab).toHaveAttribute("aria-disabled", "true");
  await expect(editor.getByRole("button", { name: "下一步：補線 →" })).toBeDisabled();
  expect(await boxLeft(editor)).toBe(cLeft);

  // 流程做完：不用重新整理，補線可用、說明消失、角色在 C 拖過的位置
  await expect.poll(async () => (await pageOut(request, target)).recompose_pending, { timeout: 60_000 }).toBe(false);
  await expect(lineTab).not.toHaveAttribute("aria-disabled", "true", { timeout: 10_000 });
  await expect(editor.getByRole("status").filter({ hasText: NOTICE })).toHaveCount(0);
  await expect.poll(() => boxLeft(editor)).toBe(cLeft);
  expect((await pageOut(request, target)).placement?.cx).toBeCloseTo(after.placement!.cx, 5);
  // 進階編輯沒有自己合成（自己合成會清掉 C 的等待，之後流程換圖時補的線就不見了）
  expect(composes).toEqual([]);
});
