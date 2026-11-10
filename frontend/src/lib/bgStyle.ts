/** 背景風格：簡潔（qwen_background，Qwen-Image-Edit-2511 無參考圖）／細緻（sdxl_background）。 */
export type BgStyle = "simple" | "detailed";

export const BG_STYLE_KEY = "coloring.bgStyle";
const DEFAULT: BgStyle = "simple";

const isBgStyle = (v: unknown): v is BgStyle => v === "simple" || v === "detailed";

/** 這個瀏覽器記住的背景風格；沒存過、值不認得或 localStorage 不能用（無痕視窗等）→ 簡潔。 */
export function readBgStyle(): BgStyle {
  try {
    const v = localStorage.getItem(BG_STYLE_KEY);
    return isBgStyle(v) ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

/** 記住背景風格；寫不進去就算了（只是少了記憶）。 */
export function writeBgStyle(s: BgStyle): void {
  try {
    localStorage.setItem(BG_STYLE_KEY, s);
  } catch {
    /* 無痕視窗、容量已滿：不影響產生 */
  }
}

/**
 * 實際要用的背景風格（同後端 decide_style）：有可用的構圖草圖（有草圖且強度 > 0）→ 細緻（只有 SDXL 會照草圖構圖）；
 * 已知簡潔不可用 → 細緻；可用狀態還沒載入或可用 → 記住的。
 * 退回細緻只影響畫面與送出，不改寫記憶：草圖移除或簡潔恢復可用後自動回到使用者選的。
 */
export function effectiveBgStyle(
  stored: BgStyle,
  opts: { hasSketch: boolean; simpleAvailable: boolean | undefined },
): BgStyle {
  if (opts.hasSketch) return "detailed";
  if (opts.simpleAvailable === false) return "detailed";
  return stored;
}
