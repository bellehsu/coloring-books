import { readFile } from "node:fs/promises";
import { expect, test, type Page as PwPage } from "@playwright/test";
import { createBook, createPage, getGenerations, grayPng, ok, setCharacter, uniqueTitle, drainQueueAfterAll } from "./seed";

drainQueueAfterAll();

/**
 * Phase 13 一鍵做完（spec §4.5、§4.1、§4.3、§4.4）：首頁「建立新繪本」→ A 做一本新的著色書（書名和紙張、主角、
 * 主題包、第 3 頁上傳背景）→「開始做這本書」→ B 整本做好了 → C「這頁可以印了」→ 印出這本書（先看一眼）→ PDF。
 * 假後端（DemoEngine）產生圖，worker 的頁面流程合成、補線、標成可以印。
 */

interface PageOut {
  id: number;
  position: number;
  scene_prompt: string;
  action_prompt: string;
  caption_zh: string | null;
  caption_en: string | null;
  background_generation_id: number | null;
  status: string;
}
const pagesOf = async (page: PwPage, bookId: number) =>
  (await ok<PageOut[]>(await page.request.get(`/api/books/${bookId}/pages`))).sort((a, b) => a.position - b.position);

async function expectNoHorizontalScroll(page: PwPage) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, `scrollWidth ${scrollWidth} > innerWidth ${innerWidth}`).toBeLessThanOrEqual(innerWidth);
}

/** main 裡看得到的控制項（連結、按鈕、輸入框、選單、包著檔案選擇的 label）高度都至少 44 px。 */
async function expectTouchTargets(page: PwPage) {
  const small = await page.evaluate(() =>
    Array.from(
      document.querySelectorAll<HTMLElement>(
        'main a, main button, main select, main textarea, main input:not([type="file"]), main label:has(input[type="file"])',
      ),
    )
      .map((el) => ({ text: (el.textContent ?? "").trim().slice(0, 20) || el.getAttribute("aria-label"), r: el.getBoundingClientRect() }))
      .filter((x) => x.r.width > 0 && x.r.height < 44)
      .map((x) => `${x.text} ${Math.round(x.r.height)}`),
  );
  expect(small).toEqual([]);
}

test("一鍵做完：建立新繪本 → 主角 → 主題包 → 第 3 頁上傳背景 → 開始做這本書 → 整本做好了 → 這頁可以印了 → 印出 PDF；390 px", async ({ page }) => {
  test.setTimeout(150_000);
  const title = uniqueTitle("e2e 一鍵");

  // ---- 1. 首頁 → 建立新繪本 ----
  await page.goto("/");
  await page.getByRole("link", { name: "建立新繪本" }).first().click();
  await expect(page).toHaveURL(/\/books\/new$/);
  await expect(page.getByRole("heading", { level: 1, name: "做一本新的著色書" })).toBeVisible();

  // ---- 2. ① 書名和紙張：離開書名欄位就建立，網址換成 /books/{id}/setup，畫面不重來 ----
  await page.getByLabel("紙張", { exact: true }).selectOption({ label: "A4・家用印表機" });
  await page.getByLabel("書名", { exact: true }).fill(title);
  const created = page.waitForResponse((r) => r.url().endsWith("/api/books") && r.request().method() === "POST");
  // 離開書名欄位（建立書）時已經在打主角描述：網址換掉之後打的字還在
  const description = page.getByLabel("主角長什麼樣子？");
  await description.fill("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");
  expect((await created).status()).toBe(201);
  await expect(page).toHaveURL(/\/books\/\d+\/setup$/);
  const bookId = Number(/\/books\/(\d+)\/setup/.exec(page.url())![1]);
  await expect(page.getByRole("heading", { level: 1, name: "做一本新的著色書" })).toBeVisible();
  await expect(page.getByLabel("書名", { exact: true })).toHaveValue(title);
  await expect(description).toHaveValue("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");

  // ---- 3. ② 主角：產生一張 → 選這隻 ----
  await page.getByRole("button", { name: "＋ 產生主角" }).click();
  await page.getByRole("button", { name: "選這隻" }).click();
  await expect(page.getByRole("button", { name: "就是牠 ✓" })).toBeVisible();
  await expect(page.getByText("選好了 ✓")).toBeVisible();
  await expect(page.getByRole("button", { name: "＋ 再畫一隻" })).toBeEnabled();

  // ---- 4. ③ 故事：套用主題包「交通工具」→ 預覽 10 頁 ----
  await page.getByRole("button", { name: "套用主題包" }).click();
  await page.getByRole("dialog", { name: "套用主題包" }).getByRole("button", { name: /交通工具/ }).click();
  await expect(page.getByText("讀到 10 段")).toBeVisible();
  const rows = page.getByRole("list", { name: "每一頁" }).getByRole("listitem");
  await expect(rows).toHaveCount(10);
  await expect(rows.nth(0)).toContainText("中文：消防車");
  await expect(rows.nth(0)).toContainText("英文：Fire Engine");
  const bar = page.getByRole("region", { name: /會畫/ });
  await expect(bar).toContainText("準備好了！會畫 20 張圖（背景 10＋動作 10）");

  // ---- 5. 第 3 頁改成上傳背景（按開始才上傳）----
  await page.getByLabel("第 3 頁上傳背景").setInputFiles({ name: "police.png", mimeType: "image/png", buffer: grayPng(1200, 1600) });
  await expect(rows.nth(2)).toContainText("（我上傳的 police.png）");
  await expect(
    page.getByRole("group", { name: "第 3 頁的背景" }).getByRole("button", { name: "AI 畫背景" }),
  ).toHaveAttribute("aria-pressed", "false");
  await expect(bar).toContainText("準備好了！會畫 19 張圖（背景 9＋動作 10）");
  await expect(bar).toContainText(/大約 \d+ 分鐘。可以先去忙，好了會通知你/);

  // ---- 6. 開始做這本書：匯入 → 上傳 → /produce → B ----
  const uploaded = page.waitForResponse((r) => /\/api\/pages\/\d+\/backgrounds\/upload$/.test(r.url()));
  const produced = page.waitForResponse((r) => r.url().endsWith(`/api/books/${bookId}/produce`));
  await page.getByRole("button", { name: "開始做這本書" }).click();
  expect((await uploaded).status()).toBe(201);
  expect((await produced).status()).toBe(202);
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}$`));

  const pages = await pagesOf(page, bookId);
  expect(pages).toHaveLength(10);
  expect(pages.map((p) => p.caption_zh)).toEqual(["消防車", "救護車", "警車", "公車", "計程車", "火車", "飛機", "帆船", "腳踏車", "直升機"]);
  const bgs = await getGenerations(page.request, bookId, "background");
  expect(bgs.filter((g) => g.page_id === pages[2].id).map((g) => g.source)).toEqual(["upload"]);
  expect(bgs.filter((g) => g.source !== "upload")).toHaveLength(9);

  // ---- 7. B：整本做好了 ----
  await expect(page.getByRole("region", { name: "整本做好了！" })).toBeVisible({ timeout: 60_000 });
  const cards = page.getByRole("list", { name: "頁面" }).getByRole("link");
  await expect(cards).toHaveCount(10);
  for (let i = 0; i < 10; i++) await expect(cards.nth(i)).toContainText("可以印 ✓");

  // ---- 8. C：打開第 2 頁 → 這頁可以印了 → 下一頁 ----
  await cards.nth(1).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}/page/${pages[1].id}$`));
  await expect(page.getByRole("heading", { level: 1, name: /^第 2 頁/ })).toBeVisible();
  await page.getByRole("button", { name: "這頁可以印了 ✓" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}/page/${pages[2].id}$`));

  // ---- 9. 印出這本書：先看一眼 → 下載 PDF ----
  await page.getByRole("link", { name: "整本書" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${bookId}$`));
  await page.getByRole("button", { name: "印出這本書" }).click();
  const sheet = page.getByRole("dialog", { name: "印出這本書" });
  await expect(sheet.getByRole("list", { name: "會印出來的頁面" }).getByRole("img")).toHaveCount(10);
  await sheet.getByRole("button", { name: "看好了，下一步 →" }).click();
  const downloading = page.waitForEvent("download");
  await sheet.getByRole("button", { name: "下載 PDF（10 頁）" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const bytes = await readFile((await download.path())!);
  expect(bytes.subarray(0, 4).toString("latin1")).toBe("%PDF");

  // ---- 10. 390 px：A 單欄、沒有水平捲動、控制項至少 44 px、底部列固定在畫面底部 ----
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/books/${bookId}/setup`);
  await expect(page.getByRole("heading", { level: 1, name: "主角與故事" })).toBeVisible();
  await expect(rows).toHaveCount(10);
  await expectNoHorizontalScroll(page);
  await expectTouchTargets(page);
  const start = page.getByRole("button", { name: "開始做這本書" });
  const box = (await start.boundingBox())!;
  expect(box.y + box.height).toBeLessThanOrEqual(844);
  expect(box.y + box.height).toBeGreaterThan(844 - 60);
  // 單欄：書名在紙張上面
  const t = (await page.getByLabel("書名", { exact: true }).boundingBox())!;
  const paper = (await page.getByLabel("紙張", { exact: true }).boundingBox())!;
  expect(paper.y).toBeGreaterThanOrEqual(t.y + t.height);
});

test("已經有的書：主角與故事列出現有頁，貼上的故事（CRLF、全形數字）只填空欄位並補頁，開始後到 B", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 補頁"));
  await setCharacter(request, book.id);
  await createPage(request, book.id, "森林裡的小木屋，門前有花", "");

  // 舊的故事頁網址轉到這裡
  await page.goto(`/books/${book.id}/story`);
  await expect(page).toHaveURL(new RegExp(`/books/${book.id}/setup$`));
  await expect(page.getByRole("heading", { level: 1, name: "主角與故事" })).toBeVisible();
  await expect(page.getByRole("link", { name: "整本書" })).toHaveAttribute("href", `/books/${book.id}`);
  const rows = page.getByRole("list", { name: "每一頁" }).getByRole("listitem");
  await expect(rows).toHaveCount(1);
  await expect(page.getByText("這本書有 1 頁。要加頁就把故事貼在上面。")).toBeVisible();

  await page.getByLabel("把故事貼在這裡").fill(
    [
      "第１頁\r\n場景：別的場景\r\n動作：開心地揮手\r\n",
      "第2頁\n場景：安靜的海邊，有貝殼\n動作：坐著看書\n",
      "第３頁\n場景：下雨的城市街道\n動作：撐著雨傘走路",
    ].join("\n"),
  );
  await expect(page.getByText("讀到 3 段（這本書已經有 1 頁：空的欄位才會填入）")).toBeVisible();
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("森林裡的小木屋，門前有花・開心地揮手");
  await expect(rows.nth(2)).toContainText("下雨的城市街道・撐著雨傘走路");
  await expect(page.getByRole("region", { name: /會畫/ })).toContainText("會畫 6 張圖（背景 3＋動作 3）");

  // ＋ 加一頁：在最後多一頁自己寫
  await page.getByRole("button", { name: "＋ 加一頁" }).click();
  await expect(rows).toHaveCount(4);
  await page.getByRole("textbox", { name: "第 4 頁場景" }).fill("山上的小木屋");
  await page.getByRole("textbox", { name: "第 4 頁動作" }).fill("看星星");
  await expect(page.getByRole("region", { name: /會畫/ })).toContainText("會畫 8 張圖（背景 4＋動作 4）");

  await page.getByRole("button", { name: "開始做這本書" }).click();
  await expect(page).toHaveURL(new RegExp(`/books/${book.id}$`));
  const pages = await pagesOf(page, book.id);
  expect(pages.map((p) => [p.scene_prompt, p.action_prompt])).toEqual([
    ["森林裡的小木屋，門前有花", "開心地揮手"],
    ["安靜的海邊，有貝殼", "坐著看書"],
    ["下雨的城市街道", "撐著雨傘走路"],
    ["山上的小木屋", "看星星"],
  ]);
  await expect(page.getByRole("region", { name: /整本做好了！|正在做這本書/ })).toBeVisible();
});

test("C：改字重新整理還在；改場景後 AI 再畫一張用新的場景", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 改字改場景"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "陽光公園", "揮手");
  expect((await request.post(`/api/books/${book.id}/produce`)).status()).toBe(202);
  await expect.poll(async () => (await pagesOf(page, book.id))[0].status, { timeout: 20_000 }).toBe("passed");

  await page.goto(`/books/${book.id}/page/${p.id}`);
  const caption = page.getByRole("listitem", { name: "圖下方的字" });
  await caption.getByRole("button", { name: "改字" }).click();
  const saved = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.url().includes(`/api/pages/${p.id}`) && (r.request().postData() ?? "").includes("caption_zh"),
  );
  await page.getByRole("textbox", { name: "中文字" }).fill("公園");
  await page.getByRole("textbox", { name: "英文字" }).focus(); // 離開中文欄位 → 存
  expect((await saved).status()).toBe(200);
  await page.reload();
  await page.getByRole("listitem", { name: "圖下方的字" }).getByRole("button", { name: "改字" }).click();
  await expect(page.getByRole("textbox", { name: "中文字" })).toHaveValue("公園");

  // 改場景 → 直接按 AI 再畫一張：先存場景，新的背景用新的場景
  const bg = page.getByRole("listitem", { name: "背景" });
  await bg.getByRole("button", { name: "改場景" }).click();
  await page.getByRole("textbox", { name: "場景描述" }).fill("下雪的公園");
  const queued = page.waitForResponse((r) => r.request().method() === "POST" && r.url().includes(`/api/pages/${p.id}/backgrounds`));
  await bg.getByRole("button", { name: "AI 再畫一張" }).click();
  expect((await queued).status()).toBe(202);
  const gens = await getGenerations(request, book.id, "background");
  expect(gens.map((g) => g.user_text).sort()).toEqual(["下雪的公園", "陽光公園"].sort());
  expect((await pagesOf(page, book.id))[0].scene_prompt).toBe("下雪的公園");
  await expect(bg).toContainText("場景：下雪的公園");
});
