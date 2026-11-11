// Phase 13 實機驗收：一鍵做一本書（真 GPU）。用法（在 frontend/ 底下）：
//   UPLOAD_BG=<png> UPLOAD_CHAR=<png> node accept_run.mjs <outdir>    BASE 預設 http://localhost:3001
// 書 1：兔子＋主題包「交通工具」＋第 3 頁上傳背景 → 開始做這本書 → B 整本做好了 → C 換一張背景→復原 → 印出 PDF
// 書 2：上傳手繪風主角＋3 頁故事 → 開始做這本書 → 整本做好了 → PDF
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";

const OUT = process.argv[2];
const BASE = process.env.BASE ?? "http://localhost:3001";
const LONG = 1_500_000;
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (m) => { const l = `[${new Date().toISOString().slice(11, 19)}] ${m}`; console.log(l); log.push(l); };
const sec = (t0) => ((Date.now() - t0) / 1000).toFixed(1);
const results = {};
const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.setDefaultTimeout(LONG);
const shot = (name, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full, animations: "disabled", timeout: 90_000 }).catch((e) => note(`shot ${name} 失敗 ${String(e).slice(0, 80)}`));

async function waitDone(bookId, label) {
  const t = Date.now();
  await page.goto(`${BASE}/books/${bookId}`);
  await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: LONG });
  const ov = await (await ctx.request.get(`${BASE}/api/books/${bookId}/overview`)).json();
  const states = ov.pages.map((p) => p.state);
  note(`${label}：整本做好了（等了 ${sec(t)} s）states=${JSON.stringify(states)} gaps=${JSON.stringify(ov.pages.map((p) => p.gap_count))}`);
  return ov;
}
async function exportPdf(bookId, name) {
  await page.goto(`${BASE}/books/${bookId}`);
  await page.getByRole("button", { name: "印出這本書" }).click();
  const sheet = page.getByRole("dialog", { name: "印出這本書" });
  await page.waitForTimeout(2000);
  await shot(`${name}_glance`);
  await sheet.getByRole("button", { name: "看好了，下一步 →" }).click();
  const dl = page.waitForEvent("download");
  await sheet.getByRole("button", { name: /^下載 PDF/ }).click();
  const d = await dl;
  await d.saveAs(`${OUT}/${name}.pdf`);
  const b = readFileSync(`${OUT}/${name}.pdf`);
  return { magic: b.subarray(0, 4).toString("latin1"), bytes: b.length };
}

try {
  // ===== 書 1 =====
  let t0 = Date.now();
  await page.goto(`${BASE}/books/new`);
  await page.getByLabel("紙張", { exact: true }).selectOption({ label: "A4・家用印表機" });
  await page.getByLabel("書名", { exact: true }).fill("Phase 13 驗收：小兔子的交通工具書");
  const created = page.waitForResponse((r) => r.url().endsWith("/api/books") && r.request().method() === "POST");
  await page.getByLabel("主角長什麼樣子？").fill("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");
  await created;
  await page.waitForURL(/\/books\/\d+\/setup$/);
  const book1 = Number(/\/books\/(\d+)\/setup/.exec(page.url())[1]);
  let t = Date.now();
  await page.getByRole("button", { name: "＋ 產生主角" }).click();
  await page.getByRole("button", { name: "選這隻" }).waitFor({ timeout: LONG });
  results.char_s = sec(t);
  await page.getByRole("button", { name: "選這隻" }).click();
  await page.getByText("選好了 ✓").waitFor();
  await page.getByRole("button", { name: "套用主題包" }).click();
  await page.getByRole("dialog", { name: "套用主題包" }).getByRole("button", { name: /交通工具/ }).click();
  await page.getByText("讀到 10 段").waitFor();
  await page.getByLabel("第 3 頁上傳背景").setInputFiles(process.env.UPLOAD_BG);
  const bar = page.getByRole("region", { name: /會畫/ });
  await page.waitForTimeout(800);
  results.bar1 = (await bar.innerText()).replace(/\s+/g, " ");
  await shot("10_book1_setup", true);
  note(`書 1：主角 ${results.char_s} s；${results.bar1}`);
  const produced = page.waitForResponse((r) => r.url().endsWith(`/api/books/${book1}/produce`));
  t = Date.now();
  await page.getByRole("button", { name: "開始做這本書" }).click();
  results.produce1_status = (await produced).status();
  await page.waitForURL(new RegExp(`/books/${book1}$`));
  await page.waitForTimeout(3000);
  await shot("11_book1_working");
  const ov1 = await waitDone(book1, "書 1");
  results.book1 = { id: book1, run_s: sec(t), total_s: sec(t0), states: ov1.pages.map((p) => p.state), gaps: ov1.pages.map((p) => p.gap_count) };
  await shot("12_book1_done", true);

  // C：第 4 頁 換一張背景 → 等新圖 → 復原
  const pages1 = await (await ctx.request.get(`${BASE}/api/books/${book1}/pages`)).json();
  pages1.sort((a, b) => a.position - b.position);
  const p4 = pages1[3];
  await page.goto(`${BASE}/books/${book1}/page/${p4.id}`);
  await page.waitForTimeout(2000);
  await shot("20_c_before");
  const before = (await (await ctx.request.get(`${BASE}/api/pages/${p4.id}`)).json().catch(() => null)) ?? p4;
  t = Date.now();
  await page.getByRole("button", { name: "AI 再畫一張" }).click();
  let after = null;
  for (let k = 0; k < 120; k++) {
    await page.waitForTimeout(3000);
    const cur = (await (await ctx.request.get(`${BASE}/api/books/${book1}/pages`)).json()).find((x) => x.id === p4.id);
    if (cur.background_generation_id !== before.background_generation_id && cur.status === "passed" && !cur.recompose_pending) { after = cur; break; }
  }
  results.c_regen_s = sec(t);
  await page.reload(); await page.waitForTimeout(2000);
  await shot("21_c_after_regen");
  await page.getByRole("button", { name: /復原/ }).first().click();
  let undone = null;
  for (let k = 0; k < 60; k++) {
    await page.waitForTimeout(2000);
    const cur = (await (await ctx.request.get(`${BASE}/api/books/${book1}/pages`)).json()).find((x) => x.id === p4.id);
    if (cur.background_generation_id === before.background_generation_id && cur.status === "passed" && !cur.recompose_pending) { undone = cur; break; }
  }
  await page.reload(); await page.waitForTimeout(2000);
  await shot("22_c_after_undo");
  results.c = { regen_ok: !!after, regen_s: results.c_regen_s, undo_ok: !!undone };
  note(`C：換一張背景 ${after ? "OK" : "失敗"}（${results.c_regen_s} s）、復原 ${undone ? "OK" : "失敗"}`);
  results.pdf1 = await exportPdf(book1, "book1");
  note(`書 1 PDF ${JSON.stringify(results.pdf1)}`);

  // ===== 書 2：上傳主角 =====
  t0 = Date.now();
  await page.goto(`${BASE}/books/new`);
  await page.getByLabel("書名", { exact: true }).fill("Phase 13 驗收：上傳的小貓");
  const created2 = page.waitForResponse((r) => r.url().endsWith("/api/books") && r.request().method() === "POST");
  await page.getByLabel("主角長什麼樣子？").fill("一隻橘色小貓，穿綠色吊帶褲");
  await created2;
  await page.waitForURL(/\/books\/\d+\/setup$/);
  const book2 = Number(/\/books\/(\d+)\/setup/.exec(page.url())[1]);
  const up = page.waitForResponse((r) => r.url().includes("/character/upload"));
  await page.getByLabel(/上傳自己的主角/).setInputFiles(process.env.UPLOAD_CHAR);
  results.char_upload_status = (await up).status();
  await page.getByText("選好了 ✓").waitFor({ timeout: 60_000 }).catch(() => {});
  const story = ["第1頁\n場景：陽光公園，有一棵大樹和長椅\n動作：開心地揮手\n中文：揮手\n英文：Hello",
    "第2頁\n場景：溫馨的廚房，桌上有一個茶壺\n動作：雙手捧著一顆紅蘋果\n中文：蘋果\n英文：Apple",
    "第3頁\n場景：海邊沙灘，有沙堡和水桶\n動作：開心地跳起來\n中文：跳跳\n英文：Jump"].join("\n");
  await page.getByLabel("把故事貼在這裡").fill(story);
  await page.getByText("讀到 3 段").waitFor();
  await page.waitForTimeout(800);
  await shot("30_book2_setup", true);
  t = Date.now();
  await page.getByRole("button", { name: "開始做這本書" }).click();
  await page.waitForURL(new RegExp(`/books/${book2}$`));
  const ov2 = await waitDone(book2, "書 2");
  results.book2 = { id: book2, run_s: sec(t), total_s: sec(t0), states: ov2.pages.map((p) => p.state), char_upload: results.char_upload_status };
  await shot("31_book2_done", true);
  results.pdf2 = await exportPdf(book2, "book2");
  note(`書 2 PDF ${JSON.stringify(results.pdf2)}`);

  // 每頁 print.png（看圖用）
  for (const [bid, tag] of [[book1, "b1"], [book2, "b2"]]) {
    const ps = (await (await ctx.request.get(`${BASE}/api/books/${bid}/pages`)).json()).sort((a, b) => a.position - b.position);
    for (const [i, p] of ps.entries()) {
      const r = await ctx.request.get(`${BASE}/api/pages/${p.id}/print.png`);
      if (r.ok()) writeFileSync(`${OUT}/print_${tag}_${String(i + 1).padStart(2, "0")}.png`, await r.body());
    }
  }
} catch (e) {
  note(`中止：${String(e).slice(0, 900)}`);
  await shot("ABORT", true);
} finally {
  writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  writeFileSync(`${OUT}/log.txt`, log.join("\n"));
  await browser.close();
}
