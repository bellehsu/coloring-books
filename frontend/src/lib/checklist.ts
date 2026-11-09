import type { Generation, Page, QA } from "@/api/client";

/**
 * done 完成（綠勾）、todo 待辦（空圈）、warn 需處理（橘色驚嘆號）、
 * skip 刻意不做（灰色，例如不畫構圖草圖；不是錯誤）。
 */
export type CheckState = "done" | "todo" | "warn" | "skip";
export type CheckKey = "background" | "pose" | "composite" | "closed" | "character" | "passed";

export interface CheckItem {
  key: CheckKey;
  label: string;
  state: CheckState;
  note: string;
}

/**
 * 頁面進度清單（純函式，6 項）。qa 未載入時給 undefined。
 * opts.selectedKind：選定圖的 generation kind（composite＝新流程合成圖；page＝舊流程圖片）；不知道時省略。
 * 沒選圖時 QA 一律忽略（可能是舊圖的快取）。QA 的 status／character_check 比頁面列表新，優先採用。
 */
export function checklist(
  page: Page,
  qa: QA | undefined,
  opts: { selectedKind?: Generation["kind"] } = {},
): CheckItem[] {
  const selected = page.selected_generation_id != null;
  const q = selected ? qa : undefined;
  const hasBg = page.background_generation_id != null;
  const hasPose = page.pose_generation_id != null;

  const background: CheckItem = hasBg
    ? { key: "background", label: "背景", state: "done", note: "已選" }
    : { key: "background", label: "背景", state: "todo", note: "還沒選背景" };

  const pose: CheckItem = hasPose
    ? { key: "pose", label: "角色動作", state: "done", note: "已選" }
    : { key: "pose", label: "角色動作", state: "todo", note: "還沒選角色動作" };

  let composite: CheckItem = { key: "composite", label: "合成", state: "todo", note: "" };
  if (selected) {
    const note = opts.selectedKind === "composite" ? "已合成" : opts.selectedKind === "page" ? "舊流程圖片" : "";
    composite = { ...composite, state: "done", note };
  } else if (hasBg && hasPose) {
    composite = { ...composite, note: "請確認合成" };
  }

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

  return [background, pose, composite, closed, character, passed];
}
