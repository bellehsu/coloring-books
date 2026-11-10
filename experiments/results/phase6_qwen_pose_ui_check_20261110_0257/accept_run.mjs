// Phase 5 實機驗收：真 GPU（ComfyUI 8188）＋瀏覽器走完新流程三頁。用法：node accept.mjs <outdir>
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";

const OUT = process.argv[2];
const BASE = "http://localhost:3020";
mkdirSync(OUT, { recursive: true });
const GEN = 300_000;
const log = [];
const note = (m) => { const l = `[${new Date().toISOString().slice(11, 19)}] ${m}`; console.log(l); log.push(l); };
const sec = (t0) => ((Date.now() - t0) / 1000).toFixed(1);

const SCENES = [
  { scene: "in a sunny park with a big tree and a bench", action: "揮手" },
  { scene: "in a cozy kitchen with a table and a teapot", action: "拿著東西" },
  { scene: "at the beach with a sandcastle and the sea", action: "跳起來" },
];

const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.setDefaultTimeout(GEN);
const results = [];
const tab = (name) => page.getByRole("tab", { name: new RegExp(`^${name}`) });

try {
  // ---- 建書＋角色 ----
  let t0 = Date.now();
  await page.goto(BASE + "/");
  await page.getByRole("button", { name: "建立新繪本" }).first().click();
  const dialog = page.getByRole("dialog", { name: "建立新繪本" });
  await dialog.getByLabel("書名").fill("Phase 6 驗收：小兔子");
  await dialog.getByRole("radio", { name: /A4/ }).click();
  await dialog.getByRole("radio", { name: /家用印表機/ }).click();
  await dialog.getByRole("button", { name: /建立，接著設定角色/ }).click();
  await page.waitForURL(/\/books\/\d+\/character$/);
  const bookId = Number(page.url().match(/books\/(\d+)/)[1]);
  await page.getByLabel("描述角色的外型").fill("a small round white bunny with long ears, wearing a red scarf");
  await page.getByRole("button", { name: "產生 4 張" }).click();
  const charCards = page.getByRole("button", { name: /^選 seed \d+$/ });
  await charCards.nth(3).waitFor();
  await page.getByRole("img").first().waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/00_character_1440.png`, fullPage: true });
  await charCards.nth(0).click();
  await page.getByRole("button", { name: "設為本書角色，開始做頁面" }).click();
  await page.waitForURL(/\/books\/\d+\/editor/);
  note(`角色：4 張候選＋設定 ${sec(t0)} s`);

  for (const [i, s] of SCENES.entries()) {
    const r = { page: i + 1, scene: s.scene, action: s.action };
    const tp = Date.now();
    try {
      await page.getByRole("button", { name: "新增頁面" }).click();
      await page.getByLabel("場景描述").fill(s.scene);
      let t = Date.now();
      await page.getByRole("button", { name: "產生背景" }).click();
      const bgCards = page.getByRole("button", { name: /^選 seed \d+ 為本頁背景$/ });
      await bgCards.first().waitFor();
      r.bg_s = sec(t);
      await bgCards.first().click();
      await page.getByRole("img", { name: "背景圖" }).waitFor();
      await page.waitForTimeout(800);
      await page.screenshot({ path: `${OUT}/p${i + 1}_1_background_1440.png` });

      await tab("角色").click();
      const modes = page.getByRole("radiogroup", { name: "產生方式" });
      r.mode_default = (await modes.getByRole("radio", { checked: true }).textContent())?.trim();
      await page.getByRole("button", { name: s.action, exact: true }).click();
      const poseCards = page.getByRole("button", { name: /^選 seed \d+ 為本頁角色動作$/ });
      const waitNew = async (n) => {
        await page.waitForFunction((k) => document.querySelectorAll('button[aria-label$="為本頁角色動作"]').length > k, n, { timeout: GEN });
      };
      if (i === 0) {
        await modes.getByRole("radio", { name: /快速/ }).click();
        const n0 = await poseCards.count();
        t = Date.now();
        await page.getByRole("button", { name: "產生角色動作" }).click();
        await waitNew(n0);
        r.pose_fast_s = sec(t);
        await modes.getByRole("radio", { name: /精準/ }).click();
      }
      const n1 = await poseCards.count();
      t = Date.now();
      await page.getByRole("button", { name: "產生角色動作" }).click();
      await waitNew(n1);
      r.pose_s = sec(t);
      await page.waitForTimeout(1500);
      r.badges = (await page.locator("body").innerText()).match(/角色動作歷史[\s\S]*/)?.[0].match(/精準|快速/g)?.join(",");
      await page.screenshot({ path: `${OUT}/p${i + 1}_2a_pose_history_1440.png` });
      await poseCards.first().click();
      await page.getByRole("img", { name: "角色動作（去背預覽）" }).waitFor();
      await page.waitForTimeout(800);
      await page.screenshot({ path: `${OUT}/p${i + 1}_2_pose_1440.png` });

      await tab("合成").click();
      const character = page.getByRole("group", { name: "角色位置" });
      await page.locator('[data-ready="true"]').first().waitFor();
      // 第 2 頁：往右移一點、放大一點，驗證合成跟畫面一致
      if (i === 1) {
        await character.focus();
        for (let k = 0; k < 10; k++) await page.keyboard.press("ArrowRight");
        await page.keyboard.press("+");
      }
      if (i === 2) {
        await character.focus();
        await page.keyboard.press("Shift+ArrowLeft");
        await page.keyboard.press("Shift+ArrowLeft");
      }
      r.placement = await page.getByText(/^水平 \d+%・垂直 \d+%・高度 \d+%$/).textContent();
      await page.screenshot({ path: `${OUT}/p${i + 1}_3_compose_1440.png` });
      t = Date.now();
      await page.getByRole("button", { name: "確認合成" }).click();
      await tab("線稿與修線").and(page.locator('[aria-selected="true"]')).waitFor();
      const lineStatus = page.getByRole("status").filter({ hasText: /缺口/ });
      await lineStatus.waitFor();
      r.compose_to_line_s = sec(t);
      r.line_status_before = (await lineStatus.textContent()).trim();
      r.gaps_before = await page.locator('[data-part="gap"]').count();
      await page.waitForTimeout(800);
      await page.screenshot({ path: `${OUT}/p${i + 1}_4_line_1440.png` });
      if (r.gaps_before > 0) {
        t = Date.now();
        await page.getByRole("toolbar", { name: "修線工具" }).getByRole("button", { name: "自動補線" }).click();
        await page.getByText(/已補 \d+ 條|沒有可補|沒有缺口|未能/).first().waitFor();
        await page.waitForTimeout(1500);
        r.autofix_s = sec(t);
        r.line_status_after = (await lineStatus.textContent()).trim();
        r.gaps_after = await page.locator('[data-part="gap"]').count();
      }

      await tab("QA 檢查").click();
      const qa = page.getByTestId("qa-panel");
      await qa.getByRole("radio", { name: "一致", exact: true }).click();
      await page.waitForTimeout(800);
      r.qa_text = (await qa.innerText()).replace(/\s+/g, " ").slice(0, 400);
      await page.screenshot({ path: `${OUT}/p${i + 1}_5_qa_1440.png` });
      const pass = qa.getByRole("button", { name: "標為通過" });
      if (await pass.isEnabled()) {
        await pass.click();
        await qa.getByRole("button", { name: /已通過/ }).waitFor({ timeout: 15000 }).then(() => (r.passed = true)).catch(() => (r.passed = false));
      } else r.passed = "button disabled";
      r.page_total_s = sec(tp);
      note(`第 ${i + 1} 頁完成 ${JSON.stringify(r)}`);
    } catch (e) {
      r.error = String(e).slice(0, 500);
      r.page_total_s = sec(tp);
      await page.screenshot({ path: `${OUT}/p${i + 1}_ERROR.png`, fullPage: true });
      note(`第 ${i + 1} 頁失敗 ${JSON.stringify(r)}`);
    }
    results.push(r);
  }

  // ---- 390 寬：合成與線稿 ----
  await page.setViewportSize({ width: 390, height: 844 });
  for (const m of ["compose", "line", "qa"]) {
    const url = new URL(page.url());
    url.searchParams.set("mode", m);
    await page.goto(url.toString());
    await page.waitForTimeout(2500);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    note(`390 ${m}: 水平溢出 ${overflow}px`);
    await page.screenshot({ path: `${OUT}/m_${m}_390.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---- 匯出 ----
  await page.getByRole("button", { name: "匯出 PDF", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "匯出 PDF" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/export_1440.png` });
  const btn = drawer.getByRole("button", { name: /^下載 PDF/ });
  const downloading = page.waitForEvent("download");
  await btn.click();
  const dl = await downloading;
  await dl.saveAs(`${OUT}/book.pdf`);
  note(`PDF：${readFileSync(`${OUT}/book.pdf`).subarray(0, 4).toString("latin1")} ${readFileSync(`${OUT}/book.pdf`).length} bytes`);
  writeFileSync(`${OUT}/book_id.txt`, String(bookId));
} catch (e) {
  note(`中止：${String(e).slice(0, 800)}`);
  await page.screenshot({ path: `${OUT}/ABORT.png`, fullPage: true });
} finally {
  writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
  writeFileSync(`${OUT}/log.txt`, log.join("\n"));
  await browser.close();
}
