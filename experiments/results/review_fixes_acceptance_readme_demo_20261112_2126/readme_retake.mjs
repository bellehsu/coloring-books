// Retake README screenshots of the existing demo book without the Next dev indicator / toasts.
import { chromium } from "playwright";

const OUT = process.argv[2];
const SHOTS = `${OUT}/readme`;
const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-software-rasterizer"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5, baseURL: "http://127.0.0.1:3001" });
const HIDE = "nextjs-portal, [data-nextjs-toast], [data-next-badge-root] { display: none !important; }";
await ctx.addInitScript((css) => {
  const add = () => { const s = document.createElement("style"); s.textContent = css; document.documentElement.appendChild(s); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add); else add();
}, HIDE);
const page = await ctx.newPage();
const settle = async (p) => {
  await p.waitForFunction(() => Array.from(document.images).every((i) => i.complete && i.naturalWidth > 0), null, { timeout: 60_000 }).catch(() => {});
  await p.waitForTimeout(1500);
};
const shot = async (name, opts = {}, p = page) => { await settle(p); await p.screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled", ...opts }); console.log("shot", name); };
const books = await (await ctx.request.get("/api/books")).json();
const bookId = books[0].id;
const pages = await (await ctx.request.get(`/api/books/${bookId}/pages`)).json();

await page.goto("/"); await shot("11_home_bookshelf");
await page.goto(`/books/${bookId}/setup`);
await page.getByRole("heading", { level: 1 }).first().waitFor();
await shot("02_setup_character");
await page.getByText(/每一頁|把故事貼在這裡/).first().scrollIntoViewIfNeeded().catch(() => {});
await page.getByRole("list", { name: "每一頁" }).scrollIntoViewIfNeeded().catch(() => {});
await shot("03_setup_story");
await page.goto(`/books/${bookId}`);
await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 60_000 });
await shot("05_overview_done");
await shot("05b_overview_done_full", { fullPage: true });
await page.goto(`/books/${bookId}/page/${pages[0].id}`);
await page.getByRole("heading", { level: 1, name: /^第 1 頁/ }).waitFor();
await shot("06_page_lineart");
await page.getByRole("radio", { name: "彩色原圖" }).click();
await shot("07_page_color");
await page.goto(`/books/${bookId}/editor`);
await page.getByRole("button", { name: "換一張角色動作" }).waitFor({ timeout: 30_000 });
await shot("08_advanced_editor");
await page.goto(`/books/${bookId}`);
await page.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 60_000 });
await page.getByRole("button", { name: "印出這本書" }).click();
await page.getByRole("dialog", { name: "印出這本書" }).waitFor();
await shot("09_print_glance");
const phone = await ctx.newPage();
await phone.setViewportSize({ width: 390, height: 844 });
await phone.goto(`/books/${bookId}`);
await phone.getByRole("region", { name: "整本做好了！" }).waitFor({ timeout: 60_000 });
await shot("10_phone_overview", {}, phone);
await browser.close();
