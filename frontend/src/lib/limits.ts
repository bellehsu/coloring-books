/** 頁面文字的長度上限，與後端一致：場景 ≤ 800 字（ScenePromptStr）、動作 1–300 字（ActionStr）。 */
export const MAX_SCENE = 800;
export const MAX_ACTION = 300;
/** 圖下方的文字，與後端一致：中文 ≤ 12 字、英文 ≤ 28 字元，都以 Unicode code point 計（𠮷 算 1）。 */
export const MAX_CAPTION_ZH = 12;
export const MAX_CAPTION_EN = 28;
export const countChars = (s: string): number => Array.from(s).length;
