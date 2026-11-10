/** 貼上整本故事：把文字解析成 (場景, 動作) 的頁面清單，並規劃要怎麼套進現有頁面。純函式，無 I/O。 */

export const MAX_IMPORT_PAGES = 40;
export const SCENE_MAX = 800;
export const ACTION_MAX = 300;

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

const HEADING = /^(第\s*\d+\s*頁|\d+\s*[.、．]|P\s*\d+|Page\s*\d+)\s*[:：]?\s*(.*)$/i;
const SCENE_LABEL = /^(場景|背景)\**\s*[:：]\s*\**\s*(.*)$/;
const ACTION_LABEL = /^(動作|角色)\**\s*[:：]\s*\**\s*(.*)$/;

const NO_LABEL = "找不到「場景：」或「動作：」";
const TOO_MANY = `超過 ${MAX_IMPORT_PAGES} 頁，這次不匯入`;

function normalize(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
}

/** 去掉行首的 * # > 與行尾的 *（Markdown 粗體／標題／引用）。 */
function cleanLine(line: string): string {
  return line.replace(/^[\s*#>]+/, "").replace(/[\s*]+$/, "");
}

function excerptOf(s: string): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > 30 ? `${t.slice(0, 30)}…` : t;
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
  const structured = cleaned.some((l) => HEADING.test(l) || SCENE_LABEL.test(l) || ACTION_LABEL.test(l));

  if (!structured && lines.some((l) => l.includes("\t"))) return finish(parseTsv(lines));

  const hasHeading = cleaned.some((l) => HEADING.test(l));
  const blocks: Block[] = [];
  let cur: Block | null = null;
  let field: "scene" | "action" | null = null;

  const feed = (line: string) => {
    const s = SCENE_LABEL.exec(line);
    const a = s ? null : ACTION_LABEL.exec(line);
    if (!cur) blocks.push((cur = newBlock()));
    cur.raw.push(line);
    if (s || a) {
      field = s ? "scene" : "action";
      cur[field] = (s ?? a)![2].trim();
    } else if (field) {
      cur[field] = join(cur[field], line);
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = cleaned[i];
    if (!line) {
      // 沒有標題時，空行分段；有標題時空行只是排版。
      if (!hasHeading) {
        cur = null;
        field = null;
      }
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      blocks.push((cur = newBlock()));
      field = null;
      if (h[2].trim()) feed(h[2].trim());
      continue;
    }
    feed(line);
  }

  return finish(
    blocks.map((b) => ({
      scene: (b.scene ?? "").trim(),
      action: (b.action ?? "").trim(),
      labelled: b.scene !== null || b.action !== null,
      raw: b.raw.join(" "),
    })),
  );
}

interface RawRow {
  scene: string;
  action: string;
  labelled: boolean;
  raw: string;
}

function parseTsv(lines: string[]): RawRow[] {
  const rows = lines.filter((l) => l.trim() !== "");
  if (rows.length && /場景|背景/.test(rows[0])) rows.shift();
  return rows.map((l) => {
    const [c1 = "", c2 = ""] = l.split("\t");
    const scene = c1.trim();
    const action = c2.trim();
    return { scene, action, labelled: scene !== "" || action !== "", raw: l };
  });
}

function finish(rows: RawRow[]): { pages: ParsedPage[]; errors: ParseError[] } {
  const pages: ParsedPage[] = [];
  const errors: ParseError[] = [];
  rows.forEach((r, i) => {
    const n = i + 1;
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
    if (p.scene.length > SCENE_MAX) problems.push(`場景超過 ${SCENE_MAX} 字`);
    if (p.action.length > ACTION_MAX) problems.push(`動作超過 ${ACTION_MAX} 字`);
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
