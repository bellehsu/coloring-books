export const ACTIONS: { label: string; phrase: string }[] = [
  { label: "站著", phrase: "standing" },
  { label: "坐著", phrase: "sitting" },
  { label: "走路", phrase: "walking" },
  { label: "跑步", phrase: "running" },
  { label: "揮手", phrase: "waving one hand" },
  { label: "指向前方", phrase: "pointing forward" },
  { label: "拿著東西", phrase: "holding something with both hands" },
  { label: "跳起來", phrase: "jumping happily" },
  { label: "看書", phrase: "reading a book" },
  { label: "開心笑", phrase: "smiling happily" },
];

const parts = (text: string) =>
  text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

/** 描述裡已有這個片語（以逗號分隔的完整項目）。 */
export function hasPhrase(text: string, phrase: string): boolean {
  return parts(text).includes(phrase);
}

/** 把片語加到描述末尾（以「, 」分隔）；已存在就原樣回傳。 */
export function togglePhrase(text: string, phrase: string): string {
  if (hasPhrase(text, phrase)) return text;
  const base = text.trim().replace(/,\s*$/, "");
  return base ? `${base}, ${phrase}` : phrase;
}
