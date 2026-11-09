import type { Page, QA } from "@/api/client";

/**
 * done 完成（綠勾）、todo 待辦（空圈）、warn 需處理（橘色驚嘆號）、
 * skip 刻意不做（灰色，例如不畫構圖草圖；不是錯誤）。
 */
export type CheckState = "done" | "todo" | "warn" | "skip";
export type CheckKey = "sketch" | "selected" | "closed" | "character" | "passed";

export interface CheckItem {
  key: CheckKey;
  label: string;
  state: CheckState;
  note: string;
}

/**
 * 頁面進度清單（純函式）。qa 未載入時給 undefined。
 * 沒選圖時 QA 一律忽略（可能是舊圖的快取）。QA 的 status／character_check 比頁面列表新，優先採用。
 */
export function checklist(page: Page, qa: QA | undefined, opts: { seed?: number } = {}): CheckItem[] {
  const selected = page.selected_generation_id != null;
  const q = selected ? qa : undefined;

  const sketch: CheckItem = page.sketch_url
    ? { key: "sketch", label: "構圖草圖", state: "done", note: "已畫" }
    : { key: "sketch", label: "構圖草圖", state: "skip", note: "未畫（不使用構圖）" };

  const sel: CheckItem = selected
    ? { key: "selected", label: "選定圖片", state: "done", note: opts.seed != null ? `seed ${opts.seed}` : "已選" }
    : { key: "selected", label: "選定圖片", state: "todo", note: "還沒選圖" };

  let closed: CheckItem = { key: "closed", label: "線條封閉", state: "todo", note: "" };
  if (selected) {
    if (!q) closed = { ...closed, note: "檢查中…" };
    else if (q.gap_count > 0) closed = { ...closed, state: "warn", note: `${q.gap_count} 個缺口` };
    else closed = { ...closed, state: "done", note: "0 個缺口" };
  }

  let character: CheckItem = { key: "character", label: "角色外型", state: "todo", note: "" };
  if (selected) {
    const check = q ? q.character_check : page.character_check;
    if (check === "consistent") character = { ...character, state: "done", note: "一致" };
    else if (check === "inconsistent") character = { ...character, state: "warn", note: "不一致" };
    else character = { ...character, note: "待確認" };
  }

  const status = q?.status ?? page.status;
  const passed: CheckItem =
    selected && status === "passed"
      ? { key: "passed", label: "通過 QA", state: "done", note: q?.forced_pass_reason ? "強制通過" : "完成" }
      : { key: "passed", label: "通過 QA", state: "todo", note: "" };

  return [sketch, sel, closed, character, passed];
}
