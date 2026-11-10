/** 貼上整本故事：把文字解析成 (場景, 動作) 的頁面清單，並規劃要怎麼套進現有頁面。純函式，無 I/O。 */

import { MAX_ACTION, MAX_SCENE } from "./limits";

export const MAX_IMPORT_PAGES = 40;

export interface ParsedPage {
  n: number;
  scene: string;
  action: string;
}
export interface ParseError {
  n: number | null;
  excerpt: string;
  reason: string;
}
export interface ExistingPage {
  position: number;
  scene_prompt: string;
  action_prompt: string;
}
export interface ImportRow {
  n: number;
  scene: string;
  action: string;
  fill: { scene: boolean; action: boolean };
  create: boolean;
  problems: string[];
}

/** 頁碼：阿拉伯數字，或一～四十的中文數字（一、十、十二、二十、三十五、四十…）。 */
const NUM = "(?:\\d+|[一二三四]?十[一二三四五六七八九]?|[一二三四五六七八九])";
/** 明確的頁標題：第N頁、P1、Page 1（後面可接冒號與同一行的內容）。 */
const PAGE_HEADING = new RegExp(`^(?:第\\s*${NUM}\\s*頁|P\\s*\\d+|Page\\s*\\d+)\\s*[:：]?\\s*(.*)$`, "i");
/** 編號式標題「1.」「1、」「一、」：小數（3.5）不算；還要看上下文（見 numberedHeading）。 */
const NUM_HEADING = new RegExp(`^(${NUM})\\s*[.、．](?!\\d)\\s*(.*)$`);
const SCENE_LABEL = /^(場景|背景)\**\s*[:：]\s*\**\s*(.*)$/;
const ACTION_LABEL = /^(動作|角色)\**\s*[:：]\s*\**\s*(.*)$/;
/** 試算表標題列的欄名。 */
const SCENE_COLS = new Set(["場景", "背景"]);
const ACTION_COLS = new Set(["動作", "角色"]);
const PAGE_COLS = new Set(["頁", "頁數"]);

const NO_LABEL = "找不到「場景：」或「動作：」";
const TOO_MANY = `超過 ${MAX_IMPORT_PAGES} 頁，這次不匯入`;

const CN_DIGITS: Record<string, number> = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };

/** NUM 比對到的頁碼轉成數字。 */
function toNumber(s: string): number {
  if (/^\d+$/.test(s)) return Number(s);
  const i = s.indexOf("十");
  if (i < 0) return CN_DIGITS[s] ?? NaN;
  const tens = i === 0 ? 1 : CN_DIGITS[s[0]];
  const ones = s.length > i + 1 ? CN_DIGITS[s[i + 1]] : 0;
  return tens * 10 + ones;
}

function normalize(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
}

/** 去掉行首的 * # > 與項目符號（- • ・），以及行尾的 *（Markdown 粗體／標題／引用／清單）。 */
function cleanLine(line: string): string {
  return line.replace(/^(?:[\s*#>•・]|-(?=\s))+/, "").replace(/[\s*]+$/, "");
}

const isLabel = (line: string) => SCENE_LABEL.test(line) || ACTION_LABEL.test(line);

function excerptOf(s: string): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > 30 ? `${t.slice(0, 30)}…` : t;
}

/**
 * 找出哪些行是頁標題，回傳每行標題後面的文字（已 cleanLine；不是標題為 null）。
 * 「第N頁／P1／Page 1」一律算。編號式「3.」「三、」要像標題才算：編號正好是下一頁，而且
 * 後面沒字、後面就是標籤、不在某個欄位的接續文字中，或下一行是標籤或標題。
 * 這樣「3.5公尺」「樹上有\n3. 隻小鳥」這類接續文字不會被切成新的一頁。
 */
function headingTexts(cleaned: string[]): (string | null)[] {
  const out: (string | null)[] = [];
  let count = 0;
  let inField = false;
  const nextLine = (i: number) => cleaned.slice(i + 1).find((l) => l !== "") ?? "";
  cleaned.forEach((line, i) => {
    if (!line) {
      inField = false;
      out.push(null);
      return;
    }
    let rest: string | null = null;
    const p = PAGE_HEADING.exec(line);
    if (p) rest = cleanLine(p[1]);
    else {
      const m = NUM_HEADING.exec(line);
      if (m && toNumber(m[1]) === count + 1) {
        const r = cleanLine(m[2]);
        const next = nextLine(i);
        if (!r || isLabel(r) || !inField || isLabel(next) || PAGE_HEADING.test(next)) rest = r;
      }
    }
    if (rest !== null) {
      count++;
      inField = isLabel(rest);
    } else if (isLabel(line)) {
      inField = true;
    }
    out.push(rest);
  });
  return out;
}

interface Block {
  scene: string | null;
  action: string | null;
  raw: string[];
}
const newBlock = (): Block => ({ scene: null, action: null, raw: [] });

function join(a: string | null, b: string): string {
  return a ? `${a}\n${b}` : b;
}

export function parseStory(text: string): { pages: ParsedPage[]; errors: ParseError[] } {
  const lines = normalize(text).split("\n");
  const cleaned = lines.map(cleanLine);
  const headings = headingTexts(cleaned);
  const hasHeading = headings.some((h) => h !== null);
  const structured = hasHeading || cleaned.some(isLabel);

  if (!structured && lines.some((l) => l.includes("\t"))) return finish(parseTsv(lines));

  const blocks: Block[] = [];
  let cur: Block | null = null;
  let field: "scene" | "action" | null = null;

  const feed = (line: string) => {
    const s = SCENE_LABEL.exec(line);
    const a = s ? null : ACTION_LABEL.exec(line);
    const f = s ? "scene" : a ? "action" : null;
    // 沒有標題時，同一段又出現已填過的標籤 → 下一頁（中間沒空行也一樣）
    if (cur && f && !hasHeading && cur[f] !== null) cur = null;
    if (!cur) blocks.push((cur = newBlock()));
    cur.raw.push(line);
    if (f) {
      field = f;
      cur[field] = (s ?? a)![2].trim();
    } else if (field) {
      cur[field] = join(cur[field], line);
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = cleaned[i];
    if (!line) {
      // 空行結束目前欄位的接續文字；沒有標題時並分段（有標題時段落由標題界定）。
      field = null;
      if (!hasHeading) cur = null;
      continue;
    }
    const h = headings[i];
    if (h !== null) {
      blocks.push((cur = newBlock()));
      field = null;
      if (h) feed(h);
      continue;
    }
    // 第一個標題之前的文字（AI 的開場白）不算任何一頁。
    if (hasHeading && !cur) continue;
    feed(line);
  }

  return finish(
    blocks.map((b) => ({
      scene: (b.scene ?? "").trim(),
      action: (b.action ?? "").trim(),
      labelled: b.scene !== null || b.action !== null,
      raw: b.raw.join(" "),
    })),
    // 沒有標題時，第一個有標籤的段落之前（開場白）與最後一個之後（結語）的段落不算頁
    !hasHeading,
  );
}

interface RawRow {
  scene: string;
  action: string;
  labelled: boolean;
  raw: string;
}

/**
 * 試算表貼上的兩欄（或更多欄）。第一列有一格剛好是欄名（場景／背景／動作／角色／頁／頁數）才當標題列，
 * 並依欄名決定哪一欄是場景、哪一欄是動作；沒有標題列時第 1 欄是場景、第 2 欄是動作。
 */
function parseTsv(lines: string[]): RawRow[] {
  const rows = lines.filter((l) => l.trim() !== "");
  let sceneCol = 0;
  let actionCol = 1;
  const head = rows.length ? rows[0].split("\t").map((c) => cleanLine(c.trim())) : [];
  if (head.some((c) => SCENE_COLS.has(c) || ACTION_COLS.has(c) || PAGE_COLS.has(c))) {
    rows.shift();
    const s = head.findIndex((c) => SCENE_COLS.has(c));
    const a = head.findIndex((c) => ACTION_COLS.has(c));
    const free = head.map((_, i) => i).filter((i) => i !== s && i !== a && !PAGE_COLS.has(head[i]));
    sceneCol = s >= 0 ? s : (free.shift() ?? 0);
    actionCol = a >= 0 ? a : (free.find((i) => i !== sceneCol) ?? sceneCol + 1);
  }
  return rows.map((l) => {
    const cells = l.split("\t");
    const scene = (cells[sceneCol] ?? "").trim();
    const action = (cells[actionCol] ?? "").trim();
    return { scene, action, labelled: scene !== "" || action !== "", raw: l };
  });
}

/** edgesAreNotPages：第一個有內容的段落之前、最後一個之後沒有標籤的段落回報為 n: null，不佔頁碼。 */
function finish(rows: RawRow[], edgesAreNotPages = false): { pages: ParsedPage[]; errors: ParseError[] } {
  const pages: ParsedPage[] = [];
  const errors: ParseError[] = [];
  const first = edgesAreNotPages ? rows.findIndex((r) => r.labelled) : 0;
  const last = edgesAreNotPages ? rows.findLastIndex((r) => r.labelled) : rows.length - 1;
  let n = 0;
  rows.forEach((r, i) => {
    if (edgesAreNotPages && (first < 0 || i < first || i > last)) {
      errors.push({ n: null, excerpt: excerptOf(r.raw), reason: NO_LABEL });
      return;
    }
    n++;
    if (n > MAX_IMPORT_PAGES) errors.push({ n, excerpt: excerptOf(r.raw), reason: TOO_MANY });
    else if (!r.labelled) errors.push({ n, excerpt: excerptOf(r.raw), reason: NO_LABEL });
    else pages.push({ n, scene: r.scene, action: r.action });
  });
  return { pages, errors };
}

/** 第 n 段對應第 n 個現有頁面（依 position）。只填空欄位；有問題的段落整段不套用。 */
export function planImport(parsed: ParsedPage[], existing: ExistingPage[]): ImportRow[] {
  const ordered = [...existing].sort((a, b) => a.position - b.position);
  return parsed.map((p) => {
    const ex = ordered[p.n - 1];
    const problems: string[] = [];
    if (p.scene.length > MAX_SCENE) problems.push(`場景超過 ${MAX_SCENE} 字`);
    if (p.action.length > MAX_ACTION) problems.push(`動作超過 ${MAX_ACTION} 字`);
    return {
      n: p.n,
      scene: p.scene,
      action: p.action,
      create: !ex,
      problems,
      fill: {
        scene: p.scene !== "" && (!ex || ex.scene_prompt.trim() === ""),
        action: p.action !== "" && (!ex || ex.action_prompt.trim() === ""),
      },
    };
  });
}
