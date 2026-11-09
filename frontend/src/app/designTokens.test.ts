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

  it("元件用到的 var(--x) 都在 globals.css 定義", () => {
    const globals = readFileSync(path.join(SRC, "app", "globals.css"), "utf8");
    const defined = new Set([...globals.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
    const missing = new Set<string>();
    for (const f of cssFiles(SRC)) {
      for (const m of readFileSync(f, "utf8").matchAll(/var\((--[\w-]+)/g)) {
        if (!defined.has(m[1]) && !m[1].startsWith("--font-")) missing.add(m[1]);
      }
    }
    expect([...missing]).toEqual([]);
  });
});
