import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import {
  createBook,
  createPage,
  getGenerations,
  getPages,
  grayPng,
  ok,
  setCharacter,
  uniqueTitle,
  uploadBackground,
  type Generation,
  drainQueueAfterAll,
} from "./seed";

drainQueueAfterAll();

const COMPOSE = /\/api\/pages\/\d+\/compose$/;
const PLACEMENT = /\/api\/pages\/\d+\/placement$/;
/** 故事頁打的動作：中文自由文字，只寫動作、不寫地點（地點寫在場景）。 */
const ACTION = "抱著一顆紅色的球開心地跳";

test("進階編輯完整流程：（A 建好的書）構圖（拖曳自動合成）→ 補線 → 完成 → 匯出 PDF", async ({ page, request }) => {
  // 建書、主角、故事與「開始做這本書」的畫面在 oneclick.spec.ts；這裡用 API 準備，專測舊的編輯器（進階編輯）
  const book = await createBook(request, uniqueTitle("e2e 小貓"));
  const bookId = book.id;
  await setCharacter(request, bookId);
  await createPage(request, bookId, "陽光公園，有一棵大樹和小草叢", ACTION);
  expect((await request.post(`/api/books/${bookId}/produce`)).status()).toBe(202);
  await expect
    .poll(async () => (await getPages(request, bookId))[0]?.status, { timeout: 20_000 })
    .toBe("passed");
  // 動作原樣（中文、不翻譯）送進角色動作
  const [pose] = await getGenerations(request, bookId, "pose");
  expect(pose.user_text).toBe(ACTION);
  await page.goto(`/books/${bookId}/editor`);

  // ---- 頁面 ① 構圖：素材已自動選用 → 開頁自動合成；拖曳角色 → 放開後再自動合成 ----
  await expect(page.getByRole("tab", { name: "① 構圖" })).toHaveAttribute("aria-selected", "true");
  const status = page.getByRole("status").filter({ hasText: /線稿已自動更新|更新中/ });
  await expect(status).toHaveText("✓ 線稿已自動更新");
  const character = page.getByRole("group", { name: "角色位置" });
  await expect(character).toHaveAttribute("data-ready", "true");

  const overlay = (await page.getByTestId("compose-overlay").boundingBox())!;
  const start = (await character.boundingBox())!;
  const [sx, sy] = [start.x + start.width / 2, start.y + start.height / 2];
  const placed = page.waitForRequest((r) => PLACEMENT.test(r.url()) && r.method() === "PUT");
  const composed = page.waitForResponse((r) => COMPOSE.test(r.url()) && r.request().method() === "POST");
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx - overlay.width * 0.02, sy + overlay.height * 0.01, { steps: 4 });
  await page.mouse.move(sx - overlay.width * 0.03, sy + overlay.height * 0.02, { steps: 4 });
  await page.mouse.up();
  const sent = (await placed).postDataJSON() as { cx: number; cy: number };
  expect(sent.cx).toBeCloseTo(0.47, 2);
  expect(sent.cy).toBeCloseTo(0.68, 2);
  const after = (await (await composed).json()) as { placement: { cx: number }; selected_generation_id: number | null };
  expect(after.placement.cx).toBeCloseTo(0.47, 2);
  expect(after.selected_generation_id).not.toBeNull();
  await expect(status).toHaveText("✓ 線稿已自動更新");

  // ---- ② 補線：橘色圈 → 自動補線 → 線條都接好了 → 角色對 → 完成這一頁 ----
  await page.getByRole("button", { name: "下一步：補線 →" }).click();
  await expect(page.getByRole("tab", { name: /^② 補線/ })).toHaveAttribute("aria-selected", "true");
  const finish = page.getByTestId("finish-panel");
  await expect(finish.getByRole("status").filter({ hasText: "還有 1 個缺口" })).toBeVisible();
  await expect(page.locator('[data-part="gap"]')).toHaveCount(1);
  await finish.getByRole("button", { name: "自動補線" }).click();
  await expect(finish.getByRole("status").filter({ hasText: "✓ 線條都接好了" })).toBeVisible();
  await expect(page.locator('[data-part="gap"]')).toHaveCount(0);
  const done = finish.getByRole("button", { name: "完成這一頁 ✓" });
  await expect(done).toBeDisabled();
  await finish.getByRole("button", { name: "對", exact: true }).click();
  await expect(finish.getByRole("button", { name: "對", exact: true })).toHaveAttribute("aria-pressed", "true");
  await done.click();
  await expect(finish.getByText("已完成")).toBeVisible();
  await expect(finish.getByRole("button", { name: "取消完成" })).toBeVisible();
  // 頁面清單的標籤變成「完成」
  const film = page.getByRole("complementary", { name: "頁面" });
  await expect(film.getByRole("listitem").first().getByText("完成", { exact: true })).toBeVisible();

  // ---- 匯出抽屜：下載 PDF ----
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  const downloading = page.waitForEvent("download");
  await drawer.getByRole("button", { name: "下載 PDF（1 頁）" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const bytes = await readFile((await download.path())!);
  expect(bytes.subarray(0, 4).toString("latin1")).toBe("%PDF");
});

test("構圖上傳背景圖：4000×800 的寬圖裁成頁面比例，縮圖有「上傳」標記", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 上傳背景"));
  const p = await createPage(request, book.id, "森林");
  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  await expect(page.getByRole("tab", { name: "① 構圖" })).toHaveAttribute("aria-selected", "true");

  const uploaded = page.waitForResponse((r) => r.url().endsWith(`/api/pages/${p.id}/backgrounds/upload`));
  await page.getByLabel("上傳背景圖").setInputFiles({ name: "wide.png", mimeType: "image/png", buffer: grayPng(4000, 800) });
  expect((await uploaded).status()).toBe(201);

  const thumbs = page.getByRole("radiogroup", { name: "選擇背景" });
  await expect(thumbs.getByRole("radio", { name: "背景第 1 張（上傳的圖片）" })).toHaveAttribute("aria-checked", "true");
  await expect(thumbs.getByText("上傳", { exact: true })).toBeVisible();
  await expect(page.getByText("上傳的圖片", { exact: true })).toBeVisible();

  // 舞台顯示裁好的背景（頁面比例，不是原圖的 5:1），還沒有角色動作
  const bg = page.getByRole("img", { name: "背景圖" });
  await expect(bg).toBeVisible();
  await expect.poll(() => bg.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
  const [w, h] = await bg.evaluate((el: HTMLImageElement) => [el.naturalWidth, el.naturalHeight]);
  expect(w / h).toBeCloseTo(864 / 1216, 2);
  await expect(page.getByRole("status").filter({ hasText: "先選好角色動作" })).toBeVisible();
  const [pg] = await getPages(request, book.id);
  expect(pg.background_generation_id).not.toBeNull();
  const [gen] = await getGenerations(request, book.id, "background");
  expect(gen.source).toBe("upload");
});

test("開始做這本書（POST /produce）後馬上打開頁面：素材完成時舞台自動填上並合成，不用重新整理", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 先開頁面"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "陽光公園", "揮手");
  // 先在另一本書排幾張角色圖（假後端每張 0.3 秒，worker 依序做）：保證頁面打開時素材還沒好
  const blocker = await createBook(request, uniqueTitle("e2e 排隊用"));
  for (let i = 0; i < 8; i++) {
    await ok(await request.post(`/api/books/${blocker.id}/character/candidates`, { data: { description: "一隻圓圓的小貓" } }));
  }
  expect((await request.post(`/api/books/${book.id}/produce`)).status()).toBe(202);

  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  await expect(page.getByText("背景與角色動作產生中…")).toBeVisible();
  // worker 完成後自動選用：編輯器自己重抓頁面 → 舞台出現 → 自動合成
  const status = page.getByRole("status").filter({ hasText: /線稿已自動更新|更新中/ });
  await expect(status).toHaveText("✓ 線稿已自動更新", { timeout: 20_000 });
  await expect(page.getByRole("group", { name: "角色位置" })).toHaveAttribute("data-ready", "true");
  const [pg] = await getPages(request, book.id);
  expect(pg.selected_generation_id).not.toBeNull();
});

test("上傳背景圖一路做到補線：角色動作自動選用 → 上傳 → 自動合成 → ② 補線完成面板", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 上傳到補線"));
  await setCharacter(request, book.id);
  const p = await createPage(request, book.id, "", "揮手");
  const pose = await ok<Generation>(await request.post(`/api/pages/${p.id}/poses`, { data: { action: "揮手" } }));
  await expect
    .poll(async () => (await getPages(request, book.id)).find((x) => x.id === p.id)?.pose_generation_id, { timeout: 20_000 })
    .toBe(pose.id);
  const uploaded = await uploadBackground(request, p.id);
  expect(uploaded.background_generation_id).not.toBeNull();
  expect(uploaded.pose_generation_id).toBe(pose.id);

  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  const thumbs = page.getByRole("radiogroup", { name: "選擇背景" });
  await expect(thumbs.getByRole("radio", { name: "背景第 1 張（上傳的圖片）" })).toHaveAttribute("aria-checked", "true");
  const status = page.getByRole("status").filter({ hasText: /線稿已自動更新|更新中/ });
  await expect(status).toHaveText("✓ 線稿已自動更新");
  const [pg] = await getPages(request, book.id);
  expect(pg.selected_generation_id).not.toBeNull();

  await page.getByRole("button", { name: "下一步：補線 →" }).click();
  await expect(page.getByRole("tab", { name: /^② 補線/ })).toHaveAttribute("aria-selected", "true");
  const finish = page.getByTestId("finish-panel");
  await expect(finish).toBeVisible();
  await expect(finish.getByText("角色看起來對嗎？")).toBeVisible();
  await expect(finish.getByRole("button", { name: "完成這一頁 ✓" })).toBeVisible();
  await expect(page.getByRole("toolbar", { name: "修線工具" })).toBeVisible();
});

test("沒設角色：角色動作「換一張」停用；前端資料過期時，後端的 409 訊息照樣顯示", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 沒有角色"));
  const p = await createPage(request, book.id, "花園", "揮手");

  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  await expect(page.getByText("這本繪本還沒設定角色，不能產生角色動作。")).toBeVisible();
  const regenerate = page.getByRole("button", { name: "換一張角色動作" });
  await expect(regenerate).toBeDisabled();
  await expect(page.getByRole("link", { name: "先設定角色" })).toBeVisible();

  // 讓畫面以為已有角色（例如另一個分頁剛設定）：按下去後端回 409，中文訊息要原樣顯示
  await page.route(`**/api/books/${book.id}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const json = await res.json();
    await route.fulfill({ response: res, json: { ...json, character_generation_id: 999_999 } });
  });
  await page.reload();
  await expect(regenerate).toBeEnabled();
  const rejected = page.waitForResponse((r) => r.url().endsWith(`/api/pages/${p.id}/poses`));
  await regenerate.click();
  expect((await rejected).status()).toBe(409);
  // 錯誤 toast 在常駐的 assertive live region 裡
  await expect(page.getByTestId("toast-assertive")).toContainText("請先設定角色");
});
