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

/** 童趣繪本（spec §2、§4.6）：色票與 WCAG AA 對比。 */
const GLOBALS = readFileSync(path.join(SRC, "app", "globals.css"), "utf8");
function token(name: string): string {
  const m = GLOBALS.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`));
  if (!m) throw new Error(`token ${name} not found as #rrggbb`);
  return m[1];
}
function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("童趣繪本 palette", () => {
  it("uses the approved spec §2 colours", () => {
    expect(token("--bg").toLowerCase()).toBe("#fff8ee");
    expect(token("--ink").toLowerCase()).toBe("#2b2420");
    expect(token("--muted").toLowerCase()).toBe("#6b5f55");
    expect(token("--accent").toLowerCase()).toBe("#f2a33a");
    expect(token("--sky").toLowerCase()).toBe("#cfe6ff");
    expect(token("--ok-soft").toLowerCase()).toBe("#d8f0d2");
    expect(token("--ok-strong").toLowerCase()).toBe("#7fc97a");
    expect(token("--warn-soft").toLowerCase()).toBe("#ffd7a6");
    expect(token("--warn-strong").toLowerCase()).toBe("#e07b1f");
    expect(token("--input-bg").toLowerCase()).toBe("#fffdf8");
  });

  it("defines the border, hard shadow and dot-ground tokens", () => {
    expect(GLOBALS).toMatch(/--border-w:\s*2px/);
    expect(GLOBALS).toMatch(/--shadow-hard:\s*0 5px 0 var\(--ink\)/);
    expect(GLOBALS).toMatch(/--shadow-hard-sm:\s*0 4px 0 var\(--ink\)/);
    expect(GLOBALS).toMatch(/radial-gradient\(var\(--dot\) 1\.2px, transparent 1\.2px\)/);
    expect(GLOBALS).toMatch(/background-size:\s*22px 22px/);
  });

  const fills = ["--bg", "--surface", "--input-bg", "--accent", "--sky", "--ok-soft", "--warn-soft", "--accent-soft", "--chip-bg", "--ok-strong"];
  it.each(fills)("ink text on %s is AA (>= 4.5:1)", (f) => {
    expect(contrast(token("--ink"), token(f))).toBeGreaterThanOrEqual(4.5);
  });

  it("white text on ink and on danger is AA", () => {
    expect(contrast("#ffffff", token("--ink"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("--on-danger"), token("--danger"))).toBeGreaterThanOrEqual(4.5);
  });

  it("muted and status text colours are AA on the ground and on white", () => {
    for (const t of ["--muted", "--muted-2", "--ok-ink", "--warn-ink", "--accent-ink", "--danger"]) {
      expect(contrast(token(t), token("--bg"))).toBeGreaterThanOrEqual(4.5);
      expect(contrast(token(t), token("--surface"))).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(token("--ok-ink"), token("--ok-soft"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("--warn-ink"), token("--warn-soft"))).toBeGreaterThanOrEqual(4.5);
  });

  it("focus ring contrasts with the ground (>= 3:1)", () => {
    expect(contrast(token("--focus"), token("--bg"))).toBeGreaterThanOrEqual(3);
    expect(contrast(token("--focus"), token("--accent"))).toBeGreaterThanOrEqual(3);
  });
});
