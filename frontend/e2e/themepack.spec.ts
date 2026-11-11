import { expect, test } from "@playwright/test";
import { createBook, uniqueTitle } from "./seed";

const NAMES = [
  ["消防車", "Fire Engine"],
  ["救護車", "Ambulance"],
  ["警車", "Police Car"],
  ["公車", "Bus"],
  ["計程車", "Taxi"],
  ["火車", "Train"],
  ["飛機", "Airplane"],
  ["帆船", "Sailboat"],
  ["腳踏車", "Bicycle"],
  ["直升機", "Helicopter"],
];

test("套用主題包「交通工具」：預覽 10 頁 → 匯入 → 每頁有場景、動作與中英文字", async ({ page, request }) => {
  const book = await createBook(request, uniqueTitle("e2e 主題包"));
  await page.goto(`/books/${book.id}/story`);
  await page.getByRole("button", { name: "套用主題包" }).click();
  await page.getByRole("dialog", { name: "套用主題包" }).getByRole("button", { name: /交通工具/ }).click();

  const dialog = page.getByRole("dialog", { name: "貼上整本故事" });
  await expect(dialog.getByText(/^10 段：新增 \d+ 頁/)).toBeVisible();
  await dialog.getByRole("button", { name: /^匯入（新增/ }).click();
  await expect(dialog).toBeHidden();

  for (const [i, [zh, en]] of NAMES.entries()) {
    const n = i + 1;
    const row = page.getByRole("article", { name: `第 ${n} 頁` });
    await expect(row).toBeVisible();
    await expect(row.getByRole("textbox", { name: `第 ${n} 頁場景` })).toHaveValue(/，沒有任何車輛、船、飛機或火車$/);
    await expect(row.getByRole("textbox", { name: `第 ${n} 頁角色動作` })).not.toHaveValue("");
    await expect(row.getByRole("textbox", { name: `第 ${n} 頁中文字` })).toHaveValue(zh);
    await expect(row.getByRole("textbox", { name: `第 ${n} 頁英文字` })).toHaveValue(en);
  }
  await expect(page.getByRole("article")).toHaveCount(10);
});
