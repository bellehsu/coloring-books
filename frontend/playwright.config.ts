import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * e2e：假生成後端（scripts/dev_fake_backend.py，不需 GPU）＋ next dev，用 chromium 跑。
 *   PYTHON=/path/to/venv/bin/python npm run e2e
 * PYTHON 預設為 `python`（需能 import 後端相依套件）；瀏覽器先跑一次 `npx playwright install chromium`。
 * 每次執行用新的暫存 storage（本機若重用已啟動的伺服器，則沿用那個伺服器的資料；測試不依賴既有資料）。
 */
const BACKEND_PORT = 8010;
const FRONTEND_PORT = 3010;
const python = process.env.PYTHON || "python";
// worker 行程也會載入這個設定檔：只在主行程建立一次暫存資料夾，經環境變數傳給 worker
process.env.E2E_STORAGE ||= mkdtempSync(path.join(tmpdir(), "coloring-books-e2e-"));
const storage = process.env.E2E_STORAGE;
const reuse = !process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? "line" : [["list"]],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    locale: "zh-TW",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // 不用 GPU 合成：WSL2 上 headless chromium 的 GPU 行程常卡住（next dev 執行時幾乎必現），
        // requestAnimationFrame 不觸發、canvas getImageData 卡死主執行緒，點擊會一直等「stable」。
        launchOptions: { args: ["--disable-gpu", "--disable-software-rasterizer"] },
      },
    },
  ],
  webServer: [
    {
      command: `env -u PYTHONPATH ${python} ../scripts/dev_fake_backend.py --port ${BACKEND_PORT} --storage ${storage}`,
      url: `http://127.0.0.1:${BACKEND_PORT}/api/system/queue`,
      reuseExistingServer: reuse,
      timeout: 60_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npx next dev -p ${FRONTEND_PORT}`,
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: reuse,
      timeout: 120_000,
    },
  ],
});
