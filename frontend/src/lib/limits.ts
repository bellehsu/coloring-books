/** 今天的生成次數用完時的說法（B 的「繼續做」、A 的「會畫幾張圖」）。 */
export const NO_QUOTA = "今天的生成次數已用完，明天再來";

/** 頁面文字的長度上限，與後端一致：場景 ≤ 800 字（ScenePromptStr）、動作 1–300 字（ActionStr）。 */
export const MAX_SCENE = 800;
export const MAX_ACTION = 300;
/** 圖下方的文字，與後端一致：中文 ≤ 12 字、英文 ≤ 28 字元，都以 Unicode code point 計（𠮷 算 1）。 */
export const MAX_CAPTION_ZH = 12;
export const MAX_CAPTION_EN = 28;
export const countChars = (s: string): number => Array.from(s).length;
/**
 * 頁面文字正規化，規則與後端 `normalize_caption`（app/services/caption_text.py）相同：
 * 先移除格式字元（Cf：零寬空格、連接字元、BOM），每一段空白（含換行、U+2028/2029、U+0085、U+001C–001F）變一個空格，
 * 再移除其餘控制字元（Cc），去頭尾。空的回傳 ""（送到後端會清掉文字）。
 */
export const normalizeCaption = (s: string): string =>
  s
    .replace(/\p{Cf}/gu, "")
    .replace(/[\s\u001c-\u001f\u0085]+/g, " ")
    .replace(/\p{Cc}/gu, "")
    .replace(/ {2,}/g, " ")
    .trim();
