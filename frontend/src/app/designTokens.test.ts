import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/** 元件 CSS 只能用 globals.css 的設計 token：不得出現十六進位色碼或 rgba()。 */
const SRC = path.resolve(__dirname, "..");

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return cssFiles(p);
    return e.name.endsWith(".css") && e.name !== "globals.css" ? [p] : [];
  });
}

describe("design tokens", () => {
  it("globals.css 以外的 CSS 沒有寫死的顏色", () => {
    const offenders: string[] = [];
    for (const f of cssFiles(SRC)) {
      readFileSync(f, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/#[0-9a-fA-F]{3,8}\b|rgba?\(/.test(line)) offenders.push(`${path.relative(SRC, f)}:${i + 1}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("4–14px 的圓角用 --radius-* token，不寫死（2–3px 的細條、999px／50% 膠囊與圓形除外）", () => {
    const offenders: string[] = [];
    for (const f of cssFiles(SRC)) {
      readFileSync(f, "utf8")
        .split("\n")
        .forEach((line, i) => {
          const m = line.match(/border-radius:\s*(\d+)px/);
          if (m && Number(m[1]) >= 4 && Number(m[1]) <= 14) offenders.push(`${path.relative(SRC, f)}:${i + 1}`);
        });
    }
    expect(offenders).toEqual([]);
  });

  it("元件用到的 var(--x) 都在 globals.css（或同一個 CSS 檔的元件區域變數）定義", () => {
    const globals = readFileSync(path.join(SRC, "app", "globals.css"), "utf8");
    const definedIn = (css: string) => new Set([...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
    const defined = definedIn(globals);
    const missing = new Set<string>();
    for (const f of cssFiles(SRC)) {
      const css = readFileSync(f, "utf8");
      const local = definedIn(css);
      for (const m of css.matchAll(/var\((--[\w-]+)/g)) {
        if (!defined.has(m[1]) && !local.has(m[1]) && !m[1].startsWith("--font-")) missing.add(m[1]);
      }
    }
    expect([...missing]).toEqual([]);
  });
});
