/**
 * 動作欄位的提示（故事頁每一頁與構圖的角色動作卡片都用 aria-describedby 連到它）：
 * 動作裡的地點會讓角色動作畫出地面或場景，地點應寫在背景的場景描述。
 */
export const ACTION_HINT = "只寫動作，地點寫在背景的場景描述（例如：寫「坐著看書」，不要寫「坐在草地上看書」）";

export const ACTIONS: { label: string; phrase: string }[] = [
  { label: "站著", phrase: "standing" },
  { label: "坐著", phrase: "sitting on the ground" },
  { label: "走路", phrase: "walking" },
  { label: "跑步", phrase: "running" },
  { label: "揮手", phrase: "waving one hand" },
  { label: "指向前方", phrase: "pointing forward" },
  { label: "拿著東西", phrase: "holding something with both hands" },
  { label: "跳起來", phrase: "jumping happily" },
  { label: "看書", phrase: "reading a book" },
  { label: "開心笑", phrase: "smiling happily" },
];

const escape = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** 描述裡已有這個片語：整詞、不分大小寫，出現在句子任何位置都算。 */
export function hasPhrase(text: string, phrase: string): boolean {
  return new RegExp(`(?<![\\w])${escape(phrase)}(?![\\w])`, "i").test(text);
}

/** 把片語加到描述末尾後的結果（以「, 」分隔）；已存在就原樣回傳。 */
export function appendPhrase(text: string, phrase: string): string {
  if (hasPhrase(text, phrase)) return text;
  const base = text.trim().replace(/,\s*$/, "");
  return base ? `${base}, ${phrase}` : phrase;
}
