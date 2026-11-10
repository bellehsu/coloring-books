// Phase 9 實機驗收（全部 Qwen＋故事步驟＋兩步驟編輯器）。用法：node accept_p9.mjs <outdir>
// 環境變數：BASE（前端，預設 http://localhost:3001）、API（後端，預設 http://127.0.0.1:8001）、UPLOAD_IMG（第 3 頁上傳用圖）
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";

const OUT = process.argv[2];
const BASE = process.env.BASE ?? "http://localhost:3001";
const API = process.env.API ?? "http://127.0.0.1:8001";
const GEN = 600_000;
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (m) => { const l = `[${new Date().toISOString().slice(11, 19)}] ${m}`; console.log(l); log.push(l); };
const sec = (t0) => ((Date.now() - t0) / 1000).toFixed(1);
const STORY = [
  "第1頁\n場景：陽光公園，有一棵大樹和一張長椅\n動作：開心地揮手打招呼\n",
  "第2頁\n場景：溫馨的廚房，桌上有一個茶壺\n動作：雙手捧著一顆紅蘋果\n",
  "第3頁\n場景：（上傳圖片）\n動作：開心地跳起來",
].join("\n");

const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.setDefaultTimeout(GEN);
const api = ctx.request;
const results = { old: {}, pages: [] };
const shot = (name, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });

try {
  // ---- Step 2：舊資料 ----
  let t = Date.now();
  const books = await (await api.get(`${API}/api/books`)).json();
  results.old.books = books.map((b) => ({ id: b.id, title: b.title }));
  await page.goto(BASE + "/");
  await page.waitForTimeout(1500);
  await shot("00_home", true);
  for (const b of books) {
    const pdf = await api.get(`${API}/api/books/${b.id}/export.pdf?include=all`);
    results.old[`book${b.id}_pdf`] = pdf.status();
    await page.goto(`${BASE}/books/${b.id}/editor`);
    await page.waitForTimeout(2500);
    await shot(`01_old_book${b.id}_editor`);
  }
  // 舊書 1 的第 1、2 頁：② 補線看得到線稿
  const b1pages = books.some((b) => b.id === 1) ? await (await api.get(`${API}/api/books/1/pages`)).json() : [];
  for (const p of b1pages.slice(0, 2)) {
    const la = await api.get(`${API}/api/pages/${p.id}/lineart`);
    results.old[`book1_page${p.id}_lineart`] = la.status();
    await page.goto(`${BASE}/books/${p.book_id}/editor?page=${p.id}&mode=line`);
    await page.waitForTimeout(2500);
    await shot(`02_old_book1_page${p.id}_line`);
  }
  note(`舊資料 ${JSON.stringify(results.old)}（${sec(t)} s）`);

  // ---- Step 3：新書，全中文 ----
  t = Date.now();
  await page.goto(BASE + "/");
  await page.getByRole("button", { name: "建立新繪本" }).first().click();
  const dlg = page.getByRole("dialog", { name: "建立新繪本" });
  await dlg.getByLabel("書名").fill("Phase 9 驗收：小兔子（全 Qwen）");
  await dlg.getByRole("radio", { name: /A4/ }).click();
  await dlg.getByRole("radio", { name: /家用印表機/ }).click();
  await dlg.getByRole("button", { name: /建立，接著設定角色/ }).click();
  await page.waitForURL(/\/books\/\d+\/character$/);
  const bookId = Number(/\/books\/(\d+)\//.exec(page.url())[1]);
  await page.getByLabel("描述角色的外型").fill("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");
  const cards = page.getByRole("button", { name: /^選 seed \d+$/ });
  let tc = Date.now();
  await page.getByRole("button", { name: "產生角色" }).click();
  await cards.nth(0).waitFor();
  results.char1_s = sec(tc);
  tc = Date.now();
  await page.getByRole("button", { name: "再產生一張" }).click();
  await cards.nth(1).waitFor();
  results.char2_s = sec(tc);
  await page.waitForTimeout(1500);
  await shot("10_character", true);
  await cards.nth(0).click();
  await page.getByRole("button", { name: "用這個角色，開始寫故事 →" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}/story$`));
  note(`角色：第一張 ${results.char1_s} s、再產生一張 ${results.char2_s} s`);

  // 故事：貼上 3 頁 → 第 3 頁改上傳圖片 → 產生全部素材
  await page.getByRole("button", { name: "貼上整本故事" }).click();
  const imp = page.getByRole("dialog", { name: "貼上整本故事" });
  await imp.getByRole("textbox", { name: "貼上故事" }).fill(STORY);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/11_paste_preview.png` });
  await imp.getByRole("button", { name: /^匯入（新增 3 頁/ }).click();
  await imp.waitFor({ state: "hidden" });
  const row = (n) => page.getByRole("article", { name: `第 ${n} 頁` });
  await row(3).waitFor();
  await row(3).getByRole("radio", { name: "上傳圖片" }).click();
  const up = page.waitForResponse((r) => r.url().includes("/backgrounds/upload"));
  await row(3).getByLabel("第 3 頁背景圖片").setInputFiles(process.env.UPLOAD_IMG);
  results.upload_status = (await up).status();
  const all = page.getByRole("button", { name: /^產生全部素材（還有 \d+ 張）$/ });
  results.batch_label = (await all.textContent())?.trim();
  const tb = Date.now();
  await all.click();
  for (const n of [1, 2, 3]) {
    await row(n).getByRole("img", { name: `第 ${n} 頁背景` }).waitFor({ timeout: GEN * 2 });
    await row(n).getByRole("img", { name: `第 ${n} 頁角色動作` }).waitFor({ timeout: GEN * 2 });
  }
  await page.getByRole("button", { name: "產生全部素材（還有 0 張）" }).waitFor();
  results.all_materials_s = sec(tb);
  await page.waitForTimeout(1500);
  await shot("12_story_done", true);
  note(`故事＋產生全部素材：上傳 ${results.upload_status}，${results.batch_label} → ${results.all_materials_s} s`);

  // 頁面：每頁 構圖（拖曳）→ 補線 → 對 → 完成
  const pages = await (await api.get(`${API}/api/books/${bookId}/pages`)).json();
  for (const [i, p] of pages.entries()) {
    const r = { page: i + 1 };
    const tp = Date.now();
    await page.goto(`${BASE}/books/${bookId}/editor?page=${p.id}`);
    const status = page.getByRole("status").filter({ hasText: /線稿已自動更新|更新中/ });
    await status.filter({ hasText: "✓ 線稿已自動更新" }).waitFor();
    r.autocompose_open_s = sec(tp);
    const character = page.getByRole("group", { name: "角色位置" });
    await page.locator('[data-ready="true"]').first().waitFor();
    const ov = await page.getByTestId("compose-overlay").boundingBox();
    const b = await character.boundingBox();
    const [sx, sy] = [b.x + b.width / 2, b.y + b.height / 2];
    let t2 = Date.now();
    const composed = page.waitForResponse((res) => /\/api\/pages\/\d+\/compose$/.test(res.url()) && res.request().method() === "POST");
    await page.mouse.move(sx, sy); await page.mouse.down();
    await page.mouse.move(sx + ov.width * (i === 1 ? 0.06 : -0.04), sy + ov.height * 0.02, { steps: 6 });
    await page.mouse.up();
    await composed;
    await status.filter({ hasText: "✓ 線稿已自動更新" }).waitFor();
    r.drag_recompose_s = sec(t2);
    await page.waitForTimeout(800);
    await shot(`2${i + 1}_p${i + 1}_compose`);
    await page.getByRole("button", { name: "下一步：補線 →" }).click();
    const fin = page.getByTestId("finish-panel");
    await fin.waitFor();
    await page.waitForTimeout(1500);
    r.gaps_before = await page.locator('[data-part="gap"]').count();
    await shot(`2${i + 1}_p${i + 1}_line_before`);
    if (r.gaps_before > 0) {
      t2 = Date.now();
      await fin.getByRole("button", { name: "自動補線" }).click();
      await page.waitForTimeout(3000);
      r.autofix_s = sec(t2);
      r.gaps_after = await page.locator('[data-part="gap"]').count();
    }
    await fin.getByRole("button", { name: "對", exact: true }).click();
    await page.waitForTimeout(800);
    await fin.getByRole("button", { name: "完成這一頁 ✓" }).click();
    const confirm = page.getByRole("dialog");
    if (await confirm.isVisible().catch(() => false)) {
      r.confirm_text = (await confirm.innerText()).replace(/\s+/g, " ").slice(0, 120);
      await confirm.getByRole("button", { name: "完成" }).click();
    }
    await fin.getByText("已完成").waitFor({ timeout: 20000 }).then(() => (r.passed = true)).catch(() => (r.passed = false));
    r.page_s = sec(tp);
    await shot(`2${i + 1}_p${i + 1}_done`);
    note(`第 ${i + 1} 頁 ${JSON.stringify(r)}`);
    results.pages.push(r);
  }

  // ---- Step 3b：舊整頁頁面（書 1 第 1 頁，驗收副本）改走新流程：上傳背景＋角色動作 → 合成取代 PAGE 圖 ----
  try {
    const t3 = Date.now();
    const lp = (await (await api.get(`${API}/api/books/1/pages`)).json())[0];
    const before = (await (await api.get(`${API}/api/books/1/generations`)).json()).find((g) => g.id === lp.selected_generation_id);
    results.legacy = { page: lp.id, before_kind: before?.kind ?? null };
    const pr = await api.post(`${API}/api/pages/${lp.id}/poses`, { data: { action: "開心地揮手" } });
    results.legacy.pose_status = pr.status();
    const ur = await api.post(`${API}/api/pages/${lp.id}/backgrounds/upload`, {
      multipart: { file: { name: "bg.png", mimeType: "image/png", buffer: readFileSync(process.env.UPLOAD_IMG) } },
    });
    results.legacy.upload_status = ur.status();
    // 上傳不自動選用 → 選它；角色動作完成後 worker 自動選用
    const upId = (await ur.json()).id;
    await api.post(`${API}/api/pages/${lp.id}/background`, { data: { generation_id: upId } });
    for (let k = 0; k < 120; k++) {
      const cur = (await (await api.get(`${API}/api/books/1/pages`)).json()).find((x) => x.id === lp.id);
      if (cur.pose_generation_id) break;
      await page.waitForTimeout(2000);
    }
    await page.goto(`${BASE}/books/1/editor?page=${lp.id}`);
    // 舊整頁頁面開頁不自動合成（設計如此）→ 像使用者一樣用方向鍵挪一下角色觸發合成
    await page.locator('[data-ready="true"]').first().waitFor();
    await page.getByRole("group", { name: "角色位置" }).focus();
    await page.keyboard.press("ArrowRight");
    await page.getByRole("status").filter({ hasText: "✓ 線稿已自動更新" }).waitFor({ timeout: 120000 });
    await page.waitForTimeout(1000);
    await shot("50_legacy_redo_compose");
    const after = (await (await api.get(`${API}/api/books/1/pages`)).json()).find((x) => x.id === lp.id);
    const ag = (await (await api.get(`${API}/api/books/1/generations`)).json()).find((g) => g.id === after.selected_generation_id);
    results.legacy.after_kind = ag?.kind ?? null;
    results.legacy.s = sec(t3);
    note(`舊頁重做 ${JSON.stringify(results.legacy)}`);
  } catch (e) {
    note(`舊頁重做失敗 ${String(e).slice(0, 400)}`);
    await page.screenshot({ path: `${OUT}/50_legacy_ERROR.png`, fullPage: true });
  }

  // 390 寬
  await page.setViewportSize({ width: 390, height: 844 });
  for (const m of ["", "&mode=line"]) {
    await page.goto(`${BASE}/books/${bookId}/editor?page=${pages[0].id}${m}`);
    await page.waitForTimeout(2500);
    const ov = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    note(`390 ${m || "compose"}：水平溢出 ${ov}px`);
    await shot(`30_m390${m ? "_line" : "_compose"}`, true);
  }
  await page.goto(`${BASE}/books/${bookId}/story`);
  await page.waitForTimeout(2000);
  note(`390 story：水平溢出 ${await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)}px`);
  await shot("31_m390_story", true);
  await page.setViewportSize({ width: 1440, height: 900 });

  // 匯出
  await page.goto(`${BASE}/books/${bookId}/editor?page=${pages[0].id}`);
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  await page.waitForTimeout(1500);
  await shot("40_export");
  const dl = page.waitForEvent("download");
  await drawer.getByRole("button", { name: /^下載 PDF/ }).click();
  const d = await dl;
  await d.saveAs(`${OUT}/book.pdf`);
  const bytes = readFileSync(`${OUT}/book.pdf`);
  note(`PDF ${bytes.subarray(0, 4).toString("latin1")} ${bytes.length} bytes`);
  results.total_story_to_pdf_s = sec(t);
  results.bookId = bookId;
} catch (e) {
  note(`中止：${String(e).slice(0, 900)}`);
  await page.screenshot({ path: `${OUT}/ABORT.png`, fullPage: true });
} finally {
  writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  writeFileSync(`${OUT}/log.txt`, log.join("\n"));
  await browser.close();
}
