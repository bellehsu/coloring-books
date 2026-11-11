// Phase 10 真 R2 驗收（假生成引擎，不用 GPU）。用法（在 frontend/ 底下，讓 @playwright/test 解析得到）：
//   LINK_ADMIN=<檔案> LINK_B=<檔案> UPLOAD_IMG=<png> node accept_run.mjs <outdir>
// BASE（前端，預設 http://localhost:3001）：所有 API 都經過 Next rewrite，cookie 才會帶上。
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";

const OUT = process.argv[2];
const BASE = process.env.BASE ?? "http://localhost:3001";
const TTL_S = Number(process.env.TTL_S ?? 120);
const T = 120_000;
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (m) => { const l = `[${new Date().toISOString().slice(11, 19)}] ${m}`; console.log(l); log.push(l); };
const sec = (t0) => ((Date.now() - t0) / 1000).toFixed(1);
const tokenPath = (f) => new URL(readFileSync(f, "utf8").trim()).pathname; // /i/<token>（不印出）
const host = (u) => { try { return new URL(u).host.replace(/^[^.]+\./, "<account>."); } catch { return "?"; } };
const STORY = [
  "第1頁\n場景：陽光公園，有一棵大樹和一張長椅\n動作：開心地揮手打招呼\n",
  "第2頁\n場景：溫馨的廚房，桌上有一個茶壺\n動作：雙手捧著一顆紅蘋果\n",
  "第3頁\n場景：（上傳圖片）\n動作：開心地跳起來",
].join("\n");

const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.setDefaultTimeout(T);
const api = ctx.request;
const results = { r2_img_responses: {}, pages: [] };
const shot = (name, full = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: full });
page.on("response", (r) => {
  if (r.url().includes(".r2.cloudflarestorage.com/")) {
    const k = `${r.status()} ${r.headers()["content-type"] ?? ""}`;
    results.r2_img_responses[k] = (results.r2_img_responses[k] ?? 0) + 1;
  }
});

try {
  // ---- 登入：admin 邀請連結 ----
  let t = Date.now();
  const anon = await api.get(`${BASE}/api/books`);
  results.no_cookie_books_status = anon.status();
  await page.goto(BASE + tokenPath(process.env.LINK_ADMIN));
  await page.waitForURL(BASE + "/");
  results.cookie = (await ctx.cookies()).map((c) => ({ name: c.name, httpOnly: c.httpOnly, sameSite: c.sameSite }));
  note(`登入：沒 cookie /api/books ${results.no_cookie_books_status}；cookie ${JSON.stringify(results.cookie)}`);

  // ---- 新書：角色 → 故事 → 產生全部素材 ----
  await page.getByRole("button", { name: "建立新繪本" }).first().click();
  const dlg = page.getByRole("dialog", { name: "建立新繪本" });
  await dlg.getByLabel("書名").fill("Phase 10 R2 驗收");
  await dlg.getByRole("radio", { name: /A4/ }).click();
  await dlg.getByRole("radio", { name: /家用印表機/ }).click();
  await dlg.getByRole("button", { name: /建立，接著設定角色/ }).click();
  await page.waitForURL(/\/books\/\d+\/character$/);
  const bookId = Number(/\/books\/(\d+)\//.exec(page.url())[1]);
  results.bookId = bookId;
  await page.getByLabel("描述角色的外型").fill("一隻圓圓的小白兔，長耳朵，圍著紅色圍巾");
  const cards = page.getByRole("button", { name: /^選 seed \d+$/ });
  await page.getByRole("button", { name: "產生角色" }).click();
  await cards.nth(0).waitFor();
  await page.getByRole("button", { name: "再產生一張" }).click();
  await cards.nth(1).waitFor();
  await page.waitForTimeout(1500);
  results.character_imgs_loaded = await page.locator("img").evaluateAll((els) => els.filter((e) => e.complete && e.naturalWidth > 0).length);
  await shot("10_character", true);
  await cards.nth(0).click();
  await page.getByRole("button", { name: "用這個角色，開始寫故事 →" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}/story$`));

  await page.getByRole("button", { name: "貼上整本故事" }).click();
  const imp = page.getByRole("dialog", { name: "貼上整本故事" });
  await imp.getByRole("textbox", { name: "貼上故事" }).fill(STORY);
  await page.waitForTimeout(800);
  await imp.getByRole("button", { name: /^匯入（新增 3 頁/ }).click();
  await imp.waitFor({ state: "hidden" });
  const row = (n) => page.getByRole("article", { name: `第 ${n} 頁` });
  await row(3).waitFor();
  await row(3).getByRole("radio", { name: "上傳圖片" }).click();
  const up = page.waitForResponse((r) => r.url().includes("/backgrounds/upload"));
  await row(3).getByLabel("第 3 頁背景圖片").setInputFiles(process.env.UPLOAD_IMG);
  results.upload_status = (await up).status();
  const tb = Date.now();
  await page.getByRole("button", { name: /^產生全部素材（還有 \d+ 張）$/ }).click();
  for (const n of [1, 2, 3]) {
    await row(n).getByRole("img", { name: `第 ${n} 頁背景` }).waitFor();
    await row(n).getByRole("img", { name: `第 ${n} 頁角色動作` }).waitFor();
  }
  await page.getByRole("button", { name: "產生全部素材（還有 0 張）" }).waitFor();
  results.all_materials_s = sec(tb);
  await page.waitForTimeout(2000);
  results.story_imgs_broken = await page.locator("img").evaluateAll((els) => els.filter((e) => e.complete && e.naturalWidth === 0).map((e) => e.alt));
  await shot("12_story_done", true);
  note(`故事：上傳 ${results.upload_status}，素材 ${results.all_materials_s} s，破圖 ${JSON.stringify(results.story_imgs_broken)}`);

  // ---- 每頁：開頁自動合成 → 補線 → 對 → 完成 ----
  const pages = await (await api.get(`${BASE}/api/books/${bookId}/pages`)).json();
  for (const [i, p] of pages.entries()) {
    const r = { page: i + 1 };
    const tp = Date.now();
    await page.goto(`${BASE}/books/${bookId}/editor?page=${p.id}`);
    await page.getByRole("status").filter({ hasText: "✓ 線稿已自動更新" }).waitFor();
    await page.locator('[data-ready="true"]').first().waitFor();
    await page.waitForTimeout(800);
    await shot(`2${i + 1}_p${i + 1}_compose`);
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
    await fin.getByRole("button", { name: "對", exact: true }).click();
    await page.waitForTimeout(800);
    await fin.getByRole("button", { name: "完成這一頁 ✓" }).click();
    const confirm = page.getByRole("dialog");
    if (await confirm.isVisible().catch(() => false)) await confirm.getByRole("button", { name: "完成" }).click();
    r.passed = await fin.getByText("已完成").waitFor({ timeout: 20000 }).then(() => true).catch(() => false);
    r.broken_imgs = await page.locator("img").evaluateAll((els) => els.filter((e) => e.complete && e.naturalWidth === 0).length);
    r.page_s = sec(tp);
    await shot(`2${i + 1}_p${i + 1}_done`);
    note(`第 ${i + 1} 頁 ${JSON.stringify(r)}`);
    results.pages.push(r);
  }

  // ---- 302 → R2 presigned、過期 ----
  const gens = await (await api.get(`${BASE}/api/books/${bookId}/generations`)).json();
  const g = gens.find((x) => x.status === "succeeded" && x.kind !== "upload") ?? gens[0];
  const red = await api.get(`${BASE}/api/generations/${g.id}/image`, { maxRedirects: 0 });
  const loc = red.headers()["location"] ?? "";
  results.redirect = { gen: g.id, kind: g.kind, status: red.status(), cache_control: red.headers()["cache-control"], location_host: host(loc),
    has_sig: /X-Amz-Signature=/.test(loc), expires_param: /X-Amz-Expires=(\d+)/.exec(loc)?.[1] ?? null };
  const fresh = await api.get(loc);
  results.redirect.fresh = { status: fresh.status(), type: fresh.headers()["content-type"], bytes: (await fresh.body()).length };
  const lineart = await api.get(`${BASE}/api/pages/${pages[0].id}/lineart`, { maxRedirects: 0 });
  results.lineart_redirect = { status: lineart.status(), host: host(lineart.headers()["location"] ?? "") };
  note(`302：${JSON.stringify(results.redirect)}；lineart ${JSON.stringify(results.lineart_redirect)}`);

  // ---- 匯出 PDF ----
  await page.goto(`${BASE}/books/${bookId}/editor?page=${pages[0].id}`);
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  await page.waitForTimeout(1500);
  const dl = page.waitForEvent("download");
  await drawer.getByRole("button", { name: /^下載 PDF/ }).click();
  await (await dl).saveAs(`${OUT}/book.pdf`);
  const bytes = readFileSync(`${OUT}/book.pdf`);
  results.pdf = { magic: bytes.subarray(0, 4).toString("latin1"), bytes: bytes.length };
  note(`PDF ${JSON.stringify(results.pdf)}`);

  // ---- 另一個邀請（非 admin，cap 2）：隔離＋每日上限 ----
  const ctxB = await browser.newContext();
  const pB = await ctxB.newPage();
  await pB.goto(BASE + tokenPath(process.env.LINK_B));
  await pB.waitForURL(BASE + "/");
  const apiB = ctxB.request;
  results.isolation = {
    books: (await (await apiB.get(`${BASE}/api/books`)).json()).length,
    book: (await apiB.get(`${BASE}/api/books/${bookId}`)).status(),
    image: (await apiB.get(`${BASE}/api/generations/${g.id}/image`, { maxRedirects: 0 })).status(),
    pdf: (await apiB.get(`${BASE}/api/books/${bookId}/export.pdf`)).status(),
  };
  const bb = await (await apiB.post(`${BASE}/api/books`, { data: { title: "B 的書", paper_size: "a4", print_mode: "home" } })).json().catch(() => ({}));
  const cap = [];
  if (bb.id) for (let k = 0; k < 3; k++) {
    const r = await apiB.post(`${BASE}/api/books/${bb.id}/character/candidates`, { data: { description: "a small red fox", count: 1 } });
    cap.push(`${r.status()}${r.status() === 429 ? " " + (await r.json()).detail : ""}`);
  }
  results.cap_b = { book_id: bb.id ?? null, candidates: cap };
  note(`B：隔離 ${JSON.stringify(results.isolation)}；上限 ${JSON.stringify(results.cap_b)}`);
  await ctxB.close();

  // ---- presigned 過期 ----
  note(`等 ${TTL_S + 15} s 讓 presigned 過期…`);
  await page.waitForTimeout((TTL_S + 15) * 1000);
  const old = await api.get(loc);
  results.redirect.expired = { status: old.status(), body_has_expired: /expired/i.test(await old.text()) };
  note(`過期後：${JSON.stringify(results.redirect.expired)}`);
} catch (e) {
  note(`中止：${String(e).slice(0, 900)}`);
  await page.screenshot({ path: `${OUT}/ABORT.png`, fullPage: true });
} finally {
  writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  writeFileSync(`${OUT}/log.txt`, log.join("\n"));
  await browser.close();
}
