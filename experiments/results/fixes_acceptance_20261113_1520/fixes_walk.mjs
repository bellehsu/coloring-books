// Fix-round 2026-11-13 acceptance walk (Task 16). Real UI :3001 → backend :8001 → ComfyUI :8188.
// Usage: node fixes_walk.mjs <OUT> <step>   (steps: setup | batch | cpage | edit | copy | export | all)
// State (bookId, pageIds) persists in <OUT>/walk_state.json so steps can be rerun.
import { chromium } from "playwright";
import fs from "node:fs";

const [, , OUT, STEP = "all"] = process.argv;
const SHOTS = `${OUT}/shots`;
fs.mkdirSync(SHOTS, { recursive: true });
const BASE = "http://127.0.0.1:3001";
const STATE = `${OUT}/walk_state.json`;
const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, "utf8")) : {};
const saveState = () => fs.writeFileSync(STATE, JSON.stringify(state, null, 1));
const t0 = Date.now();
const note = (s) => { const l = `[${((Date.now() - t0) / 1000).toFixed(0)}s] ${s}`; console.log(l); fs.appendFileSync(`${OUT}/walk.log`, l + "\n"); };

const story = [
  ["森林裡的小路，有大樹和蘑菇", "揮手打招呼", "散步", "Walk"],
  ["陽光公園的遊樂場，有溜滑梯", "開心地跳起來", "公園", "Park"],
  ["溫馨的臥室，有一張床和一扇窗戶", "抱著枕頭睡覺", "晚安", "Good Night"],
].map(([s, a, z, e], i) => [`第${i + 1}頁`, `場景：${s}`, `動作：${a}`, `中文：${z}`, `英文：${e}`].join("\n")).join("\n\n");

const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, baseURL: BASE, acceptDownloads: true });
const page = await ctx.newPage();
page.on("pageerror", (e) => note(`PAGEERROR ${e.message}`));
const imagesLoaded = (p) => p.waitForFunction(() => Array.from(document.images).every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 60_000 }).catch(() => {});
const shot = async (name, opts = {}, p = page) => { await imagesLoaded(p); await p.waitForTimeout(500); await p.screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled", ...opts }); note(`shot ${name}`); };
const api = async (path) => (await ctx.request.get(`/api${path}`)).json();
const overview = () => api(`/books/${state.bookId}/overview`);
async function waitIdle(label, timeoutS = 1800) {
  const end = Date.now() + timeoutS * 1000;
  while (Date.now() < end) {
    const o = await overview();
    const busy = (o.progress?.queued ?? 0) + (o.progress?.running ?? 0);
    if (busy === 0 && !(o.pages ?? []).some((p) => p.state === "working")) { note(`${label}: idle; progress_pages=${JSON.stringify(o.progress_pages)}`); return o; }
    await page.waitForTimeout(5000);
  }
  throw new Error(`${label}: timeout`);
}
const check = (cond, msg) => { note(`${cond ? "PASS" : "FAIL"} ${msg}`); if (!cond) state.failures = [...(state.failures ?? []), msg]; saveState(); };

async function stepSetup() {
  await page.goto("/");
  await page.getByRole("link", { name: "建立新繪本" }).first().click();
  await page.getByLabel("紙張", { exact: true }).selectOption({ label: "A4・家用印表機" });
  await page.getByLabel("書名", { exact: true }).fill("小狐狸驗收");
  await page.getByLabel("主角長什麼樣子？").fill("一隻圓圓胖胖的小狐狸，橘色的毛、白色的肚子，脖子上圍著藍色的小圍巾");
  await page.waitForURL(/\/books\/\d+\/setup$/, { timeout: 30_000 });
  state.bookId = Number(page.url().match(/books\/(\d+)\/setup/)[1]); saveState();
  // two candidates, delete one with ×, undo, delete again, choose the other
  await page.getByRole("button", { name: "＋ 產生主角" }).click();
  await page.getByRole("button", { name: "選這隻" }).first().waitFor({ timeout: 600_000 });
  await page.getByRole("button", { name: "＋ 再畫一隻" }).click();
  await page.waitForFunction(() => document.querySelectorAll('[aria-label="主角候選"] li img').length >= 2, null, { timeout: 600_000 });
  await page.waitForTimeout(1500);
  await shot("01_two_candidates");
  await page.getByRole("button", { name: "刪掉候選 2" }).click();
  await page.getByText("已刪掉這張候選").waitFor({ timeout: 15_000 });
  await shot("02_candidate_deleted_toast");
  await page.getByRole("button", { name: "復原" }).click();
  await page.waitForFunction(() => document.querySelectorAll('[aria-label="主角候選"] li img').length >= 2, null, { timeout: 30_000 });
  check(true, "candidate × then 復原 brings it back");
  await page.getByRole("button", { name: "刪掉候選 2" }).click();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: "選這隻" }).first().click();
  await page.getByText("選好了 ✓").waitFor({ timeout: 30_000 });
  await page.getByLabel("把故事貼在這裡").fill(story);
  await page.getByText(/讀到 3 段/).waitFor({ timeout: 15_000 });
  await page.getByRole("button", { name: "開始做這本書" }).click();
  await page.waitForURL(new RegExp(`/books/${state.bookId}$`), { timeout: 30_000 });
  await page.waitForTimeout(20_000);
  await shot("03_overview_working");
  const o = await waitIdle("first produce");
  state.pageIds = o.pages.map((p) => p.id); saveState();
  await page.reload(); await page.waitForTimeout(3000);
  await shot("04_overview_done");
  const line = await page.getByText(/做好 \d+ \/ \d+ 頁/).first().textContent().catch(() => "");
  check(/做好 3 \/ 3 頁/.test(line ?? ""), `progress line reads 做好 3 / 3 頁 (got: ${line})`);
}

async function stepBatch() {
  await page.goto(`/books/${state.bookId}`);
  await page.getByRole("button", { name: /選取多頁/ }).click();
  await page.getByLabel("選第 1 頁").check();
  await page.getByLabel("選第 2 頁").check();
  const bar = page.getByRole("region", { name: "重畫選取的頁" });
  await bar.getByRole("radio", { name: "2" }).check().catch(async () => { await bar.getByText("2", { exact: true }).click(); });
  await shot("05_select_many_bar");
  await bar.getByRole("button", { name: /重畫 2 頁的主角/ }).click();
  await page.waitForTimeout(15_000);
  await shot("06_batch_working");
  const o = await waitIdle("batch redraw");
  await page.reload(); await page.waitForTimeout(3000);
  await shot("07_batch_done_review");
  const reviewed = o.pages.filter((p) => p.review).map((p) => p.id);
  check(reviewed.length === 2, `2 pages tagged review_pose (got ${reviewed.length})`);
  check(o.progress_pages.ready === 3, `review pages still count as ready: ${JSON.stringify(o.progress_pages)}`);
  await page.getByRole("button", { name: /請看一下/ }).first().click().catch(() => {});
  await page.waitForTimeout(1000);
  await shot("08_filter_review");
}

async function stepCPage() {
  const pid = state.pageIds[0];
  await page.goto(`/books/${state.bookId}/page/${pid}`);
  const row = page.getByRole("list", { name: "畫過的角色動作" });
  await row.waitFor({ timeout: 30_000 });
  await page.waitForTimeout(2000);
  await shot("09_c_history_row");
  const tiles = row.getByRole("button");
  const n = await tiles.count();
  check(n >= 3, `history row lists every pose (got ${n})`);
  // click a non-current pose
  let target = -1;
  for (let i = 0; i < n; i++) if ((await tiles.nth(i).getAttribute("aria-pressed")) !== "true") { target = i; break; }
  const before = await tiles.evaluateAll((els) => els.findIndex((e) => e.getAttribute("aria-pressed") === "true"));
  await tiles.nth(target).click();
  await page.waitForFunction((t) => document.querySelectorAll('[aria-label="畫過的角色動作"] button')[t]?.getAttribute("aria-pressed") === "true", target, { timeout: 120_000 });
  await page.waitForTimeout(8000);
  await shot("10_c_switched_pose");
  await page.getByRole("button", { name: /復原/ }).first().click();
  await page.waitForFunction((b) => document.querySelectorAll('[aria-label="畫過的角色動作"] button')[b]?.getAttribute("aria-pressed") === "true", before, { timeout: 120_000 });
  check(true, "↶ 復原 returns to the previous pose");
  await page.waitForTimeout(8000);
  await shot("11_c_after_undo");
  await page.getByRole("button", { name: "重畫主角" }).click();
  await page.waitForTimeout(10_000);
  await shot("12_c_redrawing");
  await waitIdle("inline redraw");
  await page.reload(); await page.waitForTimeout(3000);
  const n2 = await page.getByRole("list", { name: "畫過的角色動作" }).getByRole("button").count();
  check(n2 === n + 1, `inline redraw adds one pose (${n} → ${n2})`);
  await shot("13_c_after_inline_redraw");
}

async function stepEdit() {
  const gensBefore = await api(`/books/${state.bookId}/generations`);
  await page.goto(`/books/${state.bookId}/setup`);
  const row = page.getByRole("listitem", { name: "第 3 頁" });
  await row.getByRole("button", { name: "編輯" }).click();
  await page.getByLabel("第 3 頁動作").fill("坐在床上打哈欠");
  await shot("14_setup_editing");
  await row.getByRole("button", { name: "儲存" }).click();
  await row.getByText("要重畫").waitFor({ timeout: 15_000 });
  await shot("15_setup_redraw_chip");
  await page.goto(`/books/${state.bookId}`);
  await page.getByText(/還有 1 頁要重畫/).first().waitFor({ timeout: 15_000 });
  await shot("16_overview_redraw_card");
  await page.getByRole("button", { name: "繼續做" }).first().click();
  await page.waitForTimeout(10_000);
  await waitIdle("setup-edit redraw");
  const gensAfter = await api(`/books/${state.bookId}/generations`);
  const newOnes = gensAfter.filter((g) => !gensBefore.some((b) => b.id === g.id) && g.kind !== "composite");
  check(newOnes.length >= 1 && newOnes.every((g) => g.page_id === state.pageIds[2]), `繼續做 drew only page 3 (${newOnes.map((g) => `${g.kind}@${g.page_id}`).join(",")})`);
  await page.reload(); await page.waitForTimeout(3000);
  await shot("17_overview_after_redraw");
  await page.goto(`/books/${state.bookId}/page/${state.pageIds[2]}`);
  await page.waitForTimeout(4000);
  await shot("18_c_page3_lineart");
}

async function stepCopy() {
  const host = process.env.LAN_HOST;
  const p2 = await ctx.newPage();
  await p2.goto(`http://${host}:3001/books/${state.bookId}/setup`);
  await p2.getByRole("button", { name: /複製給 AI 的指令/ }).click();
  const dialog = p2.getByRole("dialog", { name: "複製給 AI 的指令" });
  const toast = p2.getByText(/已複製/);
  await Promise.race([dialog.waitFor({ timeout: 10_000 }), toast.waitFor({ timeout: 10_000 })]).catch(() => {});
  const dlg = await dialog.isVisible().catch(() => false);
  check(dlg || (await toast.isVisible().catch(() => false)), `copy over http://${host}:3001 copies or shows the dialog (dialog=${dlg})`);
  await shot("19_copy_prompt_lan", {}, p2);
  await p2.close();
}

async function stepExport() {
  await page.goto(`/books/${state.bookId}`);
  await page.getByRole("button", { name: "印出這本書" }).first().click();
  await page.waitForTimeout(1500);
  await shot("20_export_sheet");
  await page.getByRole("button", { name: "看好了，下一步 →" }).click();
  await page.waitForTimeout(1500);
  await shot("20b_export_options");
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 300_000 }), page.getByRole("button", { name: /下載 PDF（3 頁）/ }).click()]);
  const pdf = `${OUT}/book.pdf`; await dl.saveAs(pdf);
  check(fs.statSync(pdf).size > 10_000, `PDF downloaded (${fs.statSync(pdf).size} bytes)`);
  await page.keyboard.press("Escape");
  // share, then delete from the bookshelf menu
  const sh = await ctx.request.put(`/api/books/${state.bookId}/share`, { data: { shared: true } });
  check(sh.ok(), `share on (${sh.status()})`);
  const before = await ctx.request.get(`/api/shared/books/${state.bookId}`);
  note(`shared GET before delete: ${before.status()}`);
  await page.goto("/");
  await page.getByRole("button", { name: "「小狐狸驗收」的選單" }).click();
  await shot("21_shelf_menu");
  await page.getByRole("menuitem", { name: "刪除這本書" }).click();
  await shot("22_delete_confirm");
  await page.getByRole("dialog").getByRole("button", { name: "刪除" }).click();
  await page.getByText("已刪除「小狐狸驗收」").waitFor({ timeout: 30_000 });
  await shot("23_deleted_toast");
  const after = await ctx.request.get(`/api/shared/books/${state.bookId}`);
  check(after.status() === 404, `shared link 404 after delete (${after.status()})`);
  const own = await ctx.request.get(`/api/books/${state.bookId}`);
  check(own.status() === 404, `book GET 404 after delete (${own.status()})`);
}

const steps = { setup: stepSetup, batch: stepBatch, cpage: stepCPage, edit: stepEdit, copy: stepCopy, export: stepExport };
try {
  for (const s of STEP === "all" ? Object.keys(steps) : STEP.split(",")) { note(`== ${s}`); await steps[s](); }
} catch (e) {
  note(`ERROR ${e.message.split("\n")[0]}`);
  await shot(`ERR_${STEP}_${Date.now()}`).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
