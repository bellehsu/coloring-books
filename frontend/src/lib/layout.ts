/** 與後端 app.services.layout.compute_layout 同規則、同結果。 */
import type { components } from "@/api/schema";

export type PaperSize = components["schemas"]["PaperSize"];
export type PrintMode = components["schemas"]["PrintMode"];
type Pair = [number, number];

export const PAPER_SIZES: PaperSize[] = ["a4", "a5", "b5", "letter", "square_21"];

export const PAPER_LABELS: Record<PaperSize, string> = {
  a4: "A4",
  a5: "A5",
  b5: "B5",
  letter: "Letter",
  square_21: "正方形",
};

export const PAPER_MM: Record<PaperSize, Pair> = {
  a4: [210, 297],
  a5: [148, 210],
  b5: [182, 257],
  letter: [215.9, 279.4],
  square_21: [210, 210],
};

export const PRINT_MODE_LABELS: Record<PrintMode, string> = {
  home: "家用印表機",
  print_shop: "印刷廠",
};

const GEN_SIZES: Pair[] = [
  [1024, 1024],
  [896, 1152],
  [864, 1216],
  [832, 1216],
  [768, 1344],
];

const HOME_MARGIN_MM = 10;
const BLEED_MM = 3;

const RATIO_GROUP: Record<PaperSize, string> = {
  a4: "iso",
  a5: "iso",
  b5: "iso",
  letter: "letter",
  square_21: "square",
};

export function ratioGroup(size: PaperSize): string {
  return RATIO_GROUP[size];
}

export interface Layout {
  paperMm: Pair;
  contentMm: Pair;
  contentPx: Pair;
  generationSize: Pair;
}

export function computeLayout(size: PaperSize, mode: PrintMode, dpi = 300): Layout {
  const [pw, ph] = PAPER_MM[size];
  const delta = mode === "home" ? -2 * HOME_MARGIN_MM : 2 * BLEED_MM;
  const cw = pw + delta;
  const ch = ph + delta;
  const toPx = (mm: number) => Math.round((mm / 25.4) * dpi);
  let best = GEN_SIZES[0];
  let bestScore = Infinity;
  for (const g of GEN_SIZES) {
    const score = Math.abs(Math.log(g[0] / g[1] / (cw / ch)));
    if (score < bestScore) {
      best = g;
      bestScore = score;
    }
  }
  return {
    paperMm: [pw, ph],
    contentMm: [cw, ch],
    contentPx: [toPx(cw), toPx(ch)],
    generationSize: best,
  };
}

/** mm → 「19 × 27.7 cm」（四捨五入到 mm） */
export function formatCm([w, h]: Pair): string {
  return `${Math.round(w) / 10} × ${Math.round(h) / 10} cm`;
}
