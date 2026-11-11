// Phase 11 實機驗收：兔子＋「交通工具」主題包 10 頁 → 產生全部素材 → 每頁合成、補線、完成 → PDF＋每頁 print.png。
// 用法（在 frontend/ 底下）：node accept_run.mjs <outdir>   環境變數 BASE（預設 http://localhost:3001）
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";

const OUT = process.argv[2];
const BASE = process.env.BASE ?? "http://localhost:3001";
const GEN = 900_000;
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (m) => { const l = `[${new Date().toISOString().slice(11, 19)}] ${m}`; console.log(l); log.push(l); };
const sec = (t0) => ((Date.now() - t0) / 1000).toFixed(1);
const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.setDefaultTimeout(GEN);
const api = ctx.request;
const results = { pages: [] };
const shot = (name, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });

try {
  const t = Date.now();
  await page.goto(BASE + "/");
  await page.getByRole("button", { name: "建立新繪本" }).first().click();
  const dlg = page.getByRole("dialog", { name: "建立新繪本" });
  await dlg.getByLabel("書名").fill("Phase 11 驗收：交通工具");
  await dlg.getByRole("radio", { name: /A4/ }).click();
  await dlg.getByRole("radio", { name: /家用印表機/ }).click();
  await dlg.getByRole("button", { name: /建立，接著設定角色/ }).click();
  await page.waitForURL(/\/books\/\d+\/character$/);
  const bookId = Number(/\/books\/(\d+)\//.exec(page.url())[1]);
  results.bookId = bookId;
  await page.getByLabel("描述角色的外型").fill("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");
  const cards = page.getByRole("button", { name: /^選 seed \d+$/ });
  let tc = Date.now();
  await page.getByRole("button", { name: "產生角色" }).click();
  await cards.nth(0).waitFor();
  results.char_s = sec(tc);
  await page.waitForTimeout(1500);
  await shot("10_character", true);
  await cards.nth(0).click();
  await page.getByRole("button", { name: "用這個角色，開始寫故事 →" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}/story$`));

  await page.getByRole("button", { name: "套用主題包" }).click();
  await page.getByRole("dialog", { name: "套用主題包" }).getByRole("button", { name: /交通工具/ }).click();
  const imp = page.getByRole("dialog", { name: "貼上整本故事" });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/11_pack_preview.png` });
  await imp.getByRole("button", { name: /^匯入（新增/ }).click();
  await imp.waitFor({ state: "hidden" });
  const row = (n) => page.getByRole("article", { name: `第 ${n} 頁` });
  await row(10).waitFor();
  const all = page.getByRole("button", { name: /^產生全部素材（還有 \d+ 張）$/ });
  results.batch_label = (await all.textContent())?.trim();
  const tb = Date.now();
  await all.click();
  for (let n = 1; n <= 10; n++) {
    await row(n).getByRole("img", { name: `第 ${n} 頁背景` }).waitFor({ timeout: GEN * 2 });
    await row(n).getByRole("img", { name: `第 ${n} 頁角色動作` }).waitFor({ timeout: GEN * 2 });
  }
  await page.getByRole("button", { name: "產生全部素材（還有 0 張）" }).waitFor();
  results.all_materials_s = sec(tb);
  await page.waitForTimeout(1500);
  await shot("12_story_done", true);
  note(`角色 ${results.char_s} s；${results.batch_label} → ${results.all_materials_s} s`);

  const pages = await (await api.get(`${BASE}/api/books/${bookId}/pages`)).json();
  for (const [i, p] of pages.entries()) {
    const r = { page: i + 1, caption: [p.caption_zh, p.caption_en] };
    const tp = Date.now();
    await page.goto(`${BASE}/books/${bookId}/editor?page=${p.id}`);
    await page.getByRole("status").filter({ hasText: "✓ 線稿已自動更新" }).waitFor();
    await page.locator('[data-ready="true"]').first().waitFor();
    await page.waitForTimeout(800);
    await shot(`2${String(i + 1).padStart(2, "0")}_compose`);
    await page.getByRole("button", { name: "下一步：補線 →" }).click();
    const fin = page.getByTestId("finish-panel");
    await fin.waitFor();
    await page.waitForTimeout(1500);
    r.gaps_before = await page.locator('[data-part="gap"]').count();
    if (r.gaps_before > 0) {
      await fin.getByRole("button", { name: "自動補線" }).click();
      await page.waitForTimeout(3000);
      r.gaps_after = await page.locator('[data-part="gap"]').count();
    }
    const qa = await (await api.get(`${BASE}/api/pages/${p.id}/qa`)).json().catch(() => ({}));
    r.qa = qa.metrics ?? qa;
    await fin.getByRole("button", { name: "對", exact: true }).click();
    await page.waitForTimeout(800);
    await fin.getByRole("button", { name: "完成這一頁 ✓" }).click();
    const confirm = page.getByRole("dialog");
    if (await confirm.isVisible().catch(() => false)) await confirm.getByRole("button", { name: "完成" }).click();
    r.passed = await fin.getByText("已完成").waitFor({ timeout: 20000 }).then(() => true).catch(() => false);
    const png = await api.get(`${BASE}/api/pages/${p.id}/print.png`);
    r.print_status = png.status();
    if (png.ok()) writeFileSync(`${OUT}/print_${String(i + 1).padStart(2, "0")}.png`, await png.body());
    r.page_s = sec(tp);
    note(`第 ${i + 1} 頁 ${JSON.stringify({ ...r, qa: undefined })}`);
    results.pages.push(r);
  }

  await page.goto(`${BASE}/books/${bookId}/editor?page=${pages[0].id}`);
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  await page.waitForTimeout(1500);
  const dl = page.waitForEvent("download");
  await drawer.getByRole("button", { name: /^下載 PDF/ }).click();
  await (await dl).saveAs(`${OUT}/book.pdf`);
  const bytes = readFileSync(`${OUT}/book.pdf`);
  results.pdf = { magic: bytes.subarray(0, 4).toString("latin1"), bytes: bytes.length };
  results.total_s = sec(t);
  note(`PDF ${JSON.stringify(results.pdf)}；總計 ${results.total_s} s`);
} catch (e) {
  note(`中止：${String(e).slice(0, 900)}`);
  await page.screenshot({ path: `${OUT}/ABORT.png`, fullPage: true });
} finally {
  writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  writeFileSync(`${OUT}/log.txt`, log.join("\n"));
  await browser.close();
}
