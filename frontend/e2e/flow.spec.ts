import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import { createBook, createPage, uniqueTitle } from "./seed";

test("完整流程：建書 → 角色 → 頁面 → 修線 → QA 通過 → 匯出 PDF", async ({ page }) => {
  const title = uniqueTitle("e2e 小貓");

  // ---- 我的繪本：建立 A4 印刷廠的書 ----
  await page.goto("/");
  await page.getByRole("button", { name: "建立新繪本" }).first().click();
  const dialog = page.getByRole("dialog", { name: "建立新繪本" });
  await dialog.getByLabel("書名").fill(title);
  await dialog.getByRole("radio", { name: /A4/ }).click();
  await dialog.getByRole("radio", { name: /印刷廠/ }).click();
  await dialog.getByRole("button", { name: /建立，接著設定角色/ }).click();

  // ---- 角色工作室：產生 4 張 → 選一張 → 設為本書角色 ----
  await expect(page).toHaveURL(/\/books\/\d+\/character$/);
  await page.getByLabel("描述角色的外型").fill("a round cat with big ears");
  await page.getByRole("button", { name: "產生 4 張" }).click();
  const charCards = page.getByRole("button", { name: /^選 seed \d+$/ });
  await expect(charCards).toHaveCount(4);
  await charCards.nth(1).click();
  await expect(charCards.nth(1)).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "設為本書角色，開始做頁面" }).click();

  // ---- 頁面編輯器：新增頁 → 畫方框 → 產生候選 → 選一張 ----
  await expect(page).toHaveURL(/\/books\/\d+\/editor/);
  await page.getByRole("button", { name: "新增頁面" }).click();
  const canvas = page.getByRole("img", { name: /構圖畫板/ });
  await expect(canvas).toBeVisible();
  await page.getByLabel("場景描述").fill("in a sunny park");
  await page.getByRole("button", { name: "方框" }).click();
  const box = (await canvas.boundingBox())!;
  // 記錄順序：草圖 PUT 完成、候選 POST 送出。畫完立刻按產生（不等 1 秒 debounce），
  // 產生必須先把草圖存好，後端在 POST 當下讀 sketch_path。
  const order: string[] = [];
  page.on("requestfinished", (r) => {
    if (/\/api\/pages\/\d+\/sketch$/.test(r.url()) && r.method() === "PUT") order.push("sketch saved");
  });
  page.on("request", (r) => {
    if (/\/api\/pages\/\d+\/candidates$/.test(r.url()) && r.method() === "POST") order.push("candidates sent");
  });
  await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.6);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.75, { steps: 5 });
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height * 0.9, { steps: 5 });
  await page.mouse.up();
  await page.getByRole("button", { name: "產生 4 張候選" }).click();
  const pageCards = page.getByRole("button", { name: /^選 seed \d+ 為本頁圖片$/ });
  await expect(pageCards).toHaveCount(4);
  expect(order).toEqual(["sketch saved", "candidates sent"]);
  await pageCards.first().click();
  await expect(page.getByText("使用中")).toBeVisible();

  // ---- 線稿與修線：看到紅圈 → 自動補線 → 缺口 0 ----
  await page.getByRole("tab", { name: /線稿與修線/ }).click();
  const lineStatus = page.getByRole("status").filter({ hasText: /缺口/ });
  await expect(lineStatus).toHaveText(/1 個缺口會讓塗色漏出去（紅圈）/);
  await expect(page.locator('[data-part="gap"]')).toHaveCount(1);
  await expect(page.getByRole("tab", { name: /QA 檢查/ })).toContainText("1");
  await page.getByRole("toolbar", { name: "修線工具" }).getByRole("button", { name: "自動補線" }).click();
  await expect(page.getByText("已補 1 條")).toBeVisible();
  await expect(page.getByText("沒有缺口")).toBeVisible();
  await expect(page.locator('[data-part="gap"]')).toHaveCount(0);

  // ---- QA：角色一致 → 標為通過 ----
  await page.getByRole("tab", { name: /QA 檢查/ }).click();
  const qa = page.getByTestId("qa-panel");
  await expect(qa.getByText("線條都有封閉")).toBeVisible();
  await qa.getByRole("radio", { name: "一致", exact: true }).click();
  await expect(qa.getByRole("radio", { name: "一致", exact: true })).toHaveAttribute("aria-checked", "true");
  await qa.getByRole("button", { name: "標為通過" }).click();
  await expect(qa.getByRole("button", { name: /已通過/ })).toBeVisible();

  // ---- 匯出抽屜：下載 PDF ----
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  await expect(drawer.getByTestId("passed-count")).toHaveText("1");
  const downloading = page.waitForEvent("download");
  await drawer.getByRole("button", { name: "下載 PDF（1 頁）" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const bytes = await readFile((await download.path())!);
  expect(bytes.subarray(0, 4).toString("latin1")).toBe("%PDF");
  await expect(drawer.getByRole("status")).toContainText("已產生");
});

test("沒設角色：不能產生頁面候選，後端的 409 訊息會顯示在畫面上", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 沒有角色"));
  const p = await createPage(request, book.id, "in a garden");

  await page.goto(`/books/${book.id}/editor?page=${p.id}`);
  // 介面先擋：按鈕停用並提示
  await expect(page.getByText("還沒設定角色，不能產生。")).toBeVisible();
  await expect(page.getByRole("button", { name: "產生 4 張候選" })).toBeDisabled();

  // 前端資料過期（例如另一個分頁的狀態）時仍可能送出：讓畫面以為已有角色，後端的 409 中文訊息要原樣顯示
  await page.route(`**/api/books/${book.id}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const res = await route.fetch();
    const json = await res.json();
    await route.fulfill({ response: res, json: { ...json, character_generation_id: 999_999 } });
  });
  await page.reload();
  const generate = page.getByRole("button", { name: "產生 4 張候選" });
  await expect(generate).toBeEnabled();
  const rejected = page.waitForResponse((r) => r.url().endsWith(`/api/pages/${p.id}/candidates`));
  await generate.click();
  expect((await rejected).status()).toBe(409);
  await expect(page.getByRole("alert").filter({ hasText: "請先設定角色" })).toBeVisible();
});
