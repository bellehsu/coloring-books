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
  { label: "拍手", phrase: "clapping hands" },
  { label: "抱大球", phrase: "hugging a big ball" },
  { label: "踢球", phrase: "kicking a ball" },
  { label: "彎腰撿東西", phrase: "bending down to pick something up" },
  { label: "伸懶腰", phrase: "stretching both arms up high" },
  { label: "跳舞", phrase: "dancing" },
  { label: "吃蘋果", phrase: "eating an apple" },
  { label: "躺著睡覺", phrase: "lying down and sleeping" },
];

/** 表情（單選）：後端不偵測，只是加在動作文字裡的英文片語。 */
export const EXPRESSIONS: { label: string; phrase: string }[] = [
  { label: "開心", phrase: "with a happy smiling expression" },
  { label: "大笑", phrase: "with a big laughing expression" },
  { label: "難過", phrase: "with a sad expression" },
  { label: "生氣", phrase: "with an angry expression" },
  { label: "驚訝", phrase: "with a surprised expression" },
  { label: "害羞", phrase: "with a shy blushing expression" },
  { label: "想睡", phrase: "with a sleepy expression" },
  { label: "害怕", phrase: "with a scared expression" },
];

/** 面向（單選）：後端以這些完整片語偵測（不分大小寫），偵測到時不用轉向變化，只用動作幅度變化（3、4）。 */
export const FACINGS: { label: string; phrase: string }[] = [
  { label: "朝左", phrase: "facing toward the left side of the picture" },
  { label: "正面", phrase: "facing the viewer" },
  { label: "朝右", phrase: "facing toward the right side of the picture" },
];

export const FACING_HINT = "臉會轉向，身體可能仍偏正面；不夠時可在構圖用左右翻轉";

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

/** 該群組目前作用中的片語（不分大小寫；手打的也算）。 */
export function activePhrase(text: string, group: { phrase: string }[]): string | null {
  return group.find((g) => hasPhrase(text, g.phrase))?.phrase ?? null;
}

/**
 * 單選群組：先移除文字裡該群組的所有片語（不分大小寫），清掉多餘的逗號，
 * 再把 phrase 加到末尾；phrase 為 null 就只移除。
 */
export function setExclusivePhrase(text: string, group: { phrase: string }[], phrase: string | null): string {
  let out = text;
  for (const g of group) {
    out = out.replace(new RegExp(`(?<![\\w])${escape(g.phrase)}(?![\\w])`, "gi"), "");
  }
  out = out
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s*[,，](?:\s*[,，])+\s*/g, ", ")
    .replace(/^[\s,，]+|[\s,，]+$/g, "")
    .replace(/\s+[,，]/g, ",");
  return phrase ? appendPhrase(out, phrase) : out;
}
