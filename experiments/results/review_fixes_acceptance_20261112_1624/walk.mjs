// Task 18 acceptance walkthrough: real UI on :3001 (backend :8001, real ComfyUI :8188).
import { chromium } from "playwright";
import fs from "node:fs";

const OUT = process.argv[2];
const BASE = "http://127.0.0.1:3001";
const shots = `${OUT}/shots`;
const log = [];
const t0 = Date.now();
const note = (s) => { const line = `[${((Date.now() - t0) / 1000).toFixed(0)}s] ${s}`; log.push(line); console.log(line); };
const fail = (s) => { note(`FAIL: ${s}`); throw new Error(s); };

const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, baseURL: BASE, acceptDownloads: true });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
const shot = async (name) => { await page.screenshot({ path: `${shots}/${name}.png`, animations: "disabled" }); note(`shot ${name}`); };
const api = async (path) => { const r = await ctx.request.get(`/api${path}`); return r.json(); };
const pagesOf = (bookId) => api(`/books/${bookId}/pages`);

let ok = true;
try {
  // 1. 新書
  await page.goto("/");
  await page.getByRole("link", { name: "建立新繪本" }).first().click();
  await page.getByLabel("紙張", { exact: true }).selectOption({ label: "A4・家用印表機" });
  await page.getByLabel("書名", { exact: true }).fill("驗收 review-fixes");
  await page.getByLabel("主角長什麼樣子？").fill("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");
  await page.waitForURL(/\/books\/\d+\/setup$/, { timeout: 30_000 });
  const bookId = Number(page.url().match(/books\/(\d+)\/setup/)[1]);
  note(`book ${bookId} created`);
  await shot("01_new_book");

  // 2. 主角（GPU）
  await page.getByRole("button", { name: "＋ 產生主角" }).click();
  note("character generating…");
  await page.getByRole("button", { name: "選這隻" }).click({ timeout: 360_000 });
  await page.getByText("選好了 ✓").waitFor({ timeout: 30_000 });
  note("character chosen");
  await shot("02_character");

  // 3. 故事（3 頁）
  const story = [
    "第1頁", "場景：陽光公園，有一棵大樹和長椅", "動作：開心地揮手", "中文：公園　英文：Park",
    "", "第2頁", "場景：安靜的海邊，有貝殼和小船", "動作：坐著看書", "中文：海邊　英文：Beach",
    "", "第3頁", "場景：麵包店門口，櫥窗裡有麵包", "動作：雙手捧著一個大麵包", "中文：麵包店　英文：Bakery",
  ].join("\n");
  await page.getByLabel("把故事貼在這裡").fill(story);
  await page.getByText("讀到 3 段").waitFor({ timeout: 10_000 });
  await shot("03_story");

  // 4. 開始做這本書 → B
  await page.getByRole("button", { name: "開始做這本書" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}$`), { timeout: 30_000 });
  note("produce started");
  await shot("04a_overview_working");
  await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 1_500_000 });
  note("all pages done");
  await shot("04b_overview_done");

  // 5. C 第 1 頁
  let pages = await pagesOf(bookId);
  const p1 = pages[0];
  await page.getByRole("list", { name: "頁面" }).getByRole("link").first().click();
  await page.waitForURL(new RegExp(`/books/${bookId}/page/${p1.id}$`));
  await page.getByRole("heading", { level: 1, name: /^第 1 頁/ }).waitFor();
  await shot("05_c_page");
  const histBefore = (await api(`/pages/${p1.id}/history`).catch(() => null));
  await page.getByRole("radio", { name: "彩色原圖" }).click();
  const stage = page.getByRole("group", { name: "角色位置" });
  await stage.focus();
  const placeBefore = JSON.stringify((await pagesOf(bookId))[0].placement);
  for (let i = 0; i < 15; i++) { await page.keyboard.press("ArrowRight"); await page.waitForTimeout(30); }
  await page.waitForTimeout(1500);
  note("moved character with 15 ArrowRight presses");
  // 等重新合成好
  for (let i = 0; i < 90; i++) { const pg = (await pagesOf(bookId))[0]; if (!pg.recompose_pending) break; await page.waitForTimeout(2000); }
  const placeAfterMove = JSON.stringify((await pagesOf(bookId))[0].placement);
  if (placeAfterMove === placeBefore) fail("placement did not change after arrow keys");
  await shot("06_c_moved");
  // 英文關掉
  await page.getByRole("radio", { name: "要印的線稿" }).click().catch(() => {});
  const en = page.getByRole("group", { name: "要印哪幾行" }).getByRole("button", { name: /英文/ });
  await en.click();
  await page.waitForTimeout(1500);
  if ((await pagesOf(bookId))[0].caption_show_en !== false) fail("英文 toggle did not save");
  await shot("07_c_english_off");
  // 復原 ×2：先回英文，再回位置（按住方向鍵只算一步）
  const undo = page.getByRole("button", { name: /復原/ }).first();
  await undo.click();
  await page.waitForTimeout(1500);
  if ((await pagesOf(bookId))[0].caption_show_en !== true) fail("undo #1 did not restore 英文");
  await undo.click();
  for (let i = 0; i < 90; i++) { const pg = (await pagesOf(bookId))[0]; if (!pg.recompose_pending) break; await page.waitForTimeout(2000); }
  const placeAfterUndo = JSON.stringify((await pagesOf(bookId))[0].placement);
  note(`placement before=${placeBefore} afterMove=${placeAfterMove} afterUndo2=${placeAfterUndo}`);
  if (placeAfterUndo !== placeBefore) fail("undo #2 did not restore the pre-move placement (held arrow key should be ONE undo step)");
  await shot("08_c_after_undo");

  // 6. 進階編輯：換一張角色動作 → 自動換上 → C 復原找回
  const poseBefore = (await pagesOf(bookId))[0].pose_generation_id;
  await page.getByRole("link", { name: "進階編輯" }).click();
  await page.waitForURL(/\/editor/);
  await page.getByRole("button", { name: "換一張角色動作" }).click({ timeout: 30_000 });
  note("editor: regenerating pose…");
  let poseNow = poseBefore;
  for (let i = 0; i < 180; i++) { poseNow = (await pagesOf(bookId))[0].pose_generation_id; if (poseNow !== poseBefore) break; await page.waitForTimeout(2000); }
  if (poseNow === poseBefore) fail("new pose was not swapped in");
  for (let i = 0; i < 90; i++) { const pg = (await pagesOf(bookId))[0]; if (!pg.recompose_pending) break; await page.waitForTimeout(2000); }
  note(`pose swapped ${poseBefore} → ${poseNow}`);
  await shot("09_editor_new_pose");
  await page.getByRole("link", { name: "回到處理這一頁" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}/page/${p1.id}$`));
  await page.getByRole("button", { name: /復原/ }).first().click();
  for (let i = 0; i < 90; i++) { const pg = (await pagesOf(bookId))[0]; if (pg.pose_generation_id === poseBefore && !pg.recompose_pending) break; await page.waitForTimeout(2000); }
  if ((await pagesOf(bookId))[0].pose_generation_id !== poseBefore) fail("C undo did not restore the old pose");
  note("C undo restored the old pose");
  await shot("10_c_undo_pose");

  // 7. 印出這本書
  await page.getByRole("link", { name: "整本書" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}$`));
  await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 600_000 });
  await page.getByRole("button", { name: "印出這本書" }).click();
  const sheet = page.getByRole("dialog", { name: "印出這本書" });
  await sheet.getByRole("button", { name: "看好了，下一步 →" }).click();
  await shot("11_print_sheet");
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 120_000 }), sheet.getByRole("button", { name: /下載 PDF/ }).click()]);
  const pdfPath = `${OUT}/book.pdf`;
  await dl.saveAs(pdfPath);
  const head = fs.readFileSync(pdfPath).subarray(0, 5).toString();
  if (head !== "%PDF-") fail("downloaded file is not a PDF");
  note(`PDF saved (${fs.statSync(pdfPath).size} bytes)`);
  // 每頁的線稿印出圖
  pages = await pagesOf(bookId);
  for (const p of pages) {
    const r = await ctx.request.get(`/api/pages/${p.id}/print.png`);
    if (r.ok()) fs.writeFileSync(`${shots}/print_page${p.position + 1}.png`, await r.body());
  }
  note(`pages: ${pages.map((p) => `${p.position + 1}:${p.status}${p.attention ? "/" + p.attention : ""}`).join(" ")}`);
} catch (e) {
  ok = false;
  note(`ERROR ${e.message}`);
  await shot("zz_error").catch(() => {});
}
note(`browser errors: ${errors.length ? errors.slice(0, 10).join(" | ") : "none"}`);
note(ok ? "RESULT: PASS" : "RESULT: FAIL");
fs.writeFileSync(`${OUT}/walk.log`, log.join("\n") + "\n");
await browser.close();
