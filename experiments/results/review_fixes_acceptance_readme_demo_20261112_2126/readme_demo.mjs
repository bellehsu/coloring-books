// README demo: one character + 20-page story, full flow, screenshots for docs. Real UI :3001 (backend :8001, ComfyUI :8188).
import { chromium } from "playwright";
import fs from "node:fs";

const OUT = process.argv[2];
const SHOTS = `${OUT}/readme`;
fs.mkdirSync(SHOTS, { recursive: true });
const BASE = "http://127.0.0.1:3001";
const t0 = Date.now();
const log = [];
const note = (s) => { const l = `[${((Date.now() - t0) / 1000).toFixed(0)}s] ${s}`; log.push(l); console.log(l); };

const pages20 = [
  ["溫暖的臥室，早晨陽光從窗戶照進來，有一張小床", "從床上坐起來伸懶腰", "起床", "Wake Up"],
  ["乾淨的浴室，有洗手台和鏡子", "拿著牙刷刷牙", "刷牙", "Brush Teeth"],
  ["廚房的餐桌，桌上有牛奶和吐司", "坐在椅子上開心地吃早餐", "早餐", "Breakfast"],
  ["家門口，有一扇大門和小花盆", "揹著書包揮手說再見", "出門", "Off We Go"],
  ["街角的公車站，有站牌和長椅", "站著等公車", "公車站", "Bus Stop"],
  ["學校大門口，有校舍和旗桿", "開心地跑向校門", "上學", "School"],
  ["教室，有黑板和課桌", "舉手回答問題", "上課", "Class Time"],
  ["圖書館，有高高的書架", "坐著看一本大書", "圖書館", "Library"],
  ["學校操場，有球門", "踢足球", "踢足球", "Soccer"],
  ["學校餐廳，有長桌", "吃便當", "午餐", "Lunch"],
  ["美術教室，有畫架和顏料", "拿著畫筆畫畫", "畫畫", "Painting"],
  ["音樂教室，有一台鋼琴", "坐著彈鋼琴", "音樂課", "Music"],
  ["公園，有大樹和鞦韆", "盪鞦韆", "盪鞦韆", "Swing"],
  ["冰淇淋店門口，有遮陽篷", "手裡拿著冰淇淋", "冰淇淋", "Ice Cream"],
  ["小花園，有很多花", "拿著澆水壺澆花", "澆花", "Watering"],
  ["池塘邊，有荷葉和小鴨子", "蹲著餵小鴨", "小鴨子", "Ducks"],
  ["下雨的街道，有水窪", "撐著雨傘走路", "下雨天", "Rainy Day"],
  ["客廳，有沙發和地毯", "坐在地毯上堆積木", "堆積木", "Blocks"],
  ["浴室裡的浴缸，有很多泡泡", "坐在浴缸裡玩泡泡", "洗澡", "Bath Time"],
  ["夜晚的臥室，窗外有月亮和星星", "抱著枕頭睡覺", "晚安", "Good Night"],
];
const story = pages20
  .map(([scene, action, zh, en], i) => [`第${i + 1}頁`, `場景：${scene}`, `動作：${action}`, `中文：${zh}`, `英文：${en}`].join("\n"))
  .join("\n\n");

const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5, baseURL: BASE, acceptDownloads: true });
const page = await ctx.newPage();
const imagesLoaded = (p) => p.waitForFunction(() => Array.from(document.images).every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 60_000 }).catch(() => {});
const shot = async (name, opts = {}, p = page) => { await imagesLoaded(p); await p.waitForTimeout(400); await p.screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled", ...opts }); note(`shot ${name}`); };
const api = async (path) => (await ctx.request.get(`/api${path}`)).json();

let ok = true;
try {
  await page.goto("/");
  await shot("00_home_empty");
  await page.getByRole("link", { name: "建立新繪本" }).first().click();
  await page.getByLabel("紙張", { exact: true }).selectOption({ label: "A4・家用印表機" });
  await page.getByLabel("書名", { exact: true }).fill("小狐狸的一天");
  await page.getByLabel("主角長什麼樣子？").fill("一隻圓圓胖胖的小狐狸，橘色的毛、白色的肚子，脖子上圍著藍色的小圍巾");
  await page.waitForURL(/\/books\/\d+\/setup$/, { timeout: 30_000 });
  const bookId = Number(page.url().match(/books\/(\d+)\/setup/)[1]);
  await shot("01_new_book");

  await page.getByRole("button", { name: "＋ 產生主角" }).click();
  await page.getByRole("button", { name: "選這隻" }).waitFor({ timeout: 360_000 });
  await shot("02_character_candidate");
  await page.getByRole("button", { name: "選這隻" }).click();
  await page.getByText("選好了 ✓").waitFor({ timeout: 30_000 });
  note("character chosen");

  await page.getByLabel("把故事貼在這裡").fill(story);
  await page.getByText(/讀到 20 段/).waitFor({ timeout: 15_000 });
  await page.getByLabel("把故事貼在這裡").scrollIntoViewIfNeeded();
  await shot("03_story");
  await shot("03b_setup_full", { fullPage: true });

  await page.getByRole("button", { name: "開始做這本書" }).click();
  await page.waitForURL(new RegExp(`/books/${bookId}$`), { timeout: 30_000 });
  await page.waitForTimeout(60_000);
  await shot("04_overview_working");
  await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 2_700_000 });
  note("all 20 pages done");
  await page.waitForTimeout(3000);
  await shot("05_overview_done");
  await shot("05b_overview_done_full", { fullPage: true });

  // phone view of the overview
  const phone = await ctx.newPage();
  await phone.setViewportSize({ width: 390, height: 844 });
  await phone.goto(`/books/${bookId}`);
  await phone.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 60_000 });
  await shot("10_phone_overview", {}, phone);
  await phone.close();

  // C page: line art + color original
  const pages = await api(`/books/${bookId}/pages`);
  await page.getByRole("list", { name: "頁面" }).getByRole("link").first().click();
  await page.waitForURL(new RegExp(`/books/${bookId}/page/${pages[0].id}$`));
  await page.getByRole("heading", { level: 1, name: /^第 1 頁/ }).waitFor();
  await shot("06_page_lineart");
  await page.getByRole("radio", { name: "彩色原圖" }).click();
  await shot("07_page_color");

  // advanced editor
  await page.getByRole("link", { name: "進階編輯" }).click();
  await page.waitForURL(/\/editor/);
  await page.getByRole("button", { name: "換一張角色動作" }).waitFor({ timeout: 30_000 });
  await shot("08_advanced_editor");

  // print
  await page.goto(`/books/${bookId}`);
  await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 60_000 });
  await page.getByRole("button", { name: "印出這本書" }).click();
  const sheet = page.getByRole("dialog", { name: "印出這本書" });
  await sheet.waitFor();
  await shot("09_print_glance");
  await sheet.getByRole("button", { name: "看好了，下一步 →" }).click();
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 300_000 }), sheet.getByRole("button", { name: /下載 PDF/ }).click()]);
  await dl.saveAs(`${OUT}/小狐狸的一天.pdf`);
  note(`PDF saved ${fs.statSync(`${OUT}/小狐狸的一天.pdf`).size} bytes`);

  // bookshelf
  await page.goto("/");
  await shot("11_home_bookshelf");

  // printed pages
  for (const p of pages) {
    const r = await ctx.request.get(`/api/pages/${p.id}/print.png`);
    if (r.ok()) fs.writeFileSync(`${SHOTS}/print_${String(p.position + 1).padStart(2, "0")}.png`, await r.body());
  }
  const final = await api(`/books/${bookId}/pages`);
  note(`pages: ${final.length}, states: ${final.map((p) => p.status + (p.attention ? "/" + p.attention : "")).join(",")}`);
} catch (e) {
  ok = false;
  note(`ERROR ${e.message}`);
  await page.screenshot({ path: `${SHOTS}/zz_error.png` }).catch(() => {});
}
note(ok ? "RESULT: PASS" : "RESULT: FAIL");
fs.writeFileSync(`${OUT}/readme_demo.log`, log.join("\n") + "\n");
await browser.close();
