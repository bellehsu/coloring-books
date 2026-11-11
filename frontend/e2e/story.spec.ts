import { expect, test } from "@playwright/test";
import { createBook, createPage, setCharacter, uniqueTitle } from "./seed";

/** 3 頁分鏡：第 1 頁用 CRLF 換行、第 1 與第 3 頁用全形數字。 */
const PASTE = [
  "第１頁\r\n場景：森林裡的小木屋，門前有花\r\n動作：開心地揮手\r\n",
  "第2頁\n場景：安靜的海邊，有貝殼\n動作：坐著看書\n",
  "第３頁\n場景：下雨的城市街道\n動作：撐著雨傘走路",
].join("\n");

test("貼上整本故事：3 頁（CRLF、全形數字）→ 預覽 → 匯入 → 產生全部素材 → 縮圖出現", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 故事"));
  await setCharacter(request, book.id);
  await page.goto(`/books/${book.id}/story`);
  await expect(page.getByRole("heading", { name: "寫下每一頁的故事" })).toBeVisible();

  await page.getByRole("button", { name: "貼上整本故事" }).click();
  const dialog = page.getByRole("dialog", { name: "貼上整本故事" });
  await dialog.getByRole("textbox", { name: "貼上故事" }).fill(PASTE);

  // 預覽：3 列，頁碼 1–3，場景與動作都拆對（CRLF 不留在文字裡）
  const rows = dialog.getByRole("table").getByRole("row");
  await expect(rows).toHaveCount(4); // 表頭 + 3 頁
  for (const [i, scene, action] of [
    [1, "森林裡的小木屋，門前有花", "開心地揮手"],
    [2, "安靜的海邊，有貝殼", "坐著看書"],
    [3, "下雨的城市街道", "撐著雨傘走路"],
  ] as const) {
    const cells = rows.nth(i).getByRole("cell");
    await expect(cells.nth(0)).toHaveText(String(i));
    await expect(cells.nth(1)).toHaveText(scene);
    await expect(cells.nth(2)).toHaveText(action);
  }
  await expect(dialog.getByText(/^3 段：新增 3 頁/)).toBeVisible();
  await dialog.getByRole("button", { name: /^匯入（新增 3 頁/ }).click();
  await expect(dialog).toBeHidden();

  // 故事頁：3 列依序填好
  const row = (n: number) => page.getByRole("article", { name: `第 ${n} 頁` });
  await expect(row(3)).toBeVisible();
  await expect(row(1).getByRole("textbox", { name: "第 1 頁場景" })).toHaveValue("森林裡的小木屋，門前有花");
  await expect(row(1).getByRole("textbox", { name: "第 1 頁角色動作" })).toHaveValue("開心地揮手");
  await expect(row(3).getByRole("textbox", { name: "第 3 頁角色動作" })).toHaveValue("撐著雨傘走路");

  // 產生全部素材：3 頁 × 背景＋角色動作 → 6 張縮圖
  await page.getByRole("button", { name: "產生全部素材（還有 6 張）" }).click();
  for (const n of [1, 2, 3]) {
    await expect(row(n).getByRole("img", { name: `第 ${n} 頁背景` })).toBeVisible();
    await expect(row(n).getByRole("img", { name: `第 ${n} 頁角色動作` })).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "產生全部素材（還有 0 張）" })).toBeDisabled();
});

test("圖下方的文字：在故事列輸入、離開欄位就存，重新整理還在；390 px 沒有橫向捲動", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 圖下方文字"));
  await createPage(request, book.id, "消防局門口", "開著紅色消防車");
  await page.goto(`/books/${book.id}/story`);
  const row = page.getByRole("article", { name: "第 1 頁" });
  const savedZh = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.url().includes("/api/pages/") && (r.request().postData() ?? "").includes("caption_zh"),
  );
  await row.getByRole("textbox", { name: "第 1 頁中文字" }).fill("消防車");
  await expect(row.getByText("還可 9 字")).toBeVisible();
  const saved = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && r.url().includes("/api/pages/") && (r.request().postData() ?? "").includes("Fire Engine"),
  );
  await row.getByRole("textbox", { name: "第 1 頁英文字" }).fill("Fire Engine");
  await row.getByRole("textbox", { name: "第 1 頁中文字" }).focus(); // 離開英文欄位 → 存
  await Promise.all([saved, savedZh]);
  await page.reload();
  const again = page.getByRole("article", { name: "第 1 頁" });
  await expect(again.getByRole("textbox", { name: "第 1 頁中文字" })).toHaveValue("消防車");
  await expect(again.getByRole("textbox", { name: "第 1 頁英文字" })).toHaveValue("Fire Engine");

  // 超過上限：訊息出現，不存
  await again.getByRole("textbox", { name: "第 1 頁英文字" }).fill("x".repeat(40));
  await expect(again.getByText("英文字最多 28 個字元")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
});
