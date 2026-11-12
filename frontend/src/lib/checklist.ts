import type { Generation, Page, QA } from "@/api/client";
import { recomposeRunning } from "@/api/hooks";
import { STATE_LABEL } from "@/features/book/overviewState";

/**
 * done 完成（綠勾）、todo 待辦（空圈）、warn 需處理（橘色驚嘆號）、
 * skip 刻意不做（灰色；不是錯誤）。
 */
export type CheckState = "done" | "todo" | "warn" | "skip";
export type CheckKey = "compose" | "line" | "passed";

export interface CheckItem {
  key: CheckKey;
  label: string;
  state: CheckState;
  note: string;
}

/**
 * 頁面進度清單（純函式，3 項：構圖、補線、完成）。qa 未載入時給 undefined。
 * opts.selectedKind：選定圖的 generation kind（composite＝合成圖；page＝舊流程圖片）；不知道時省略。
 * 沒選圖時 QA 一律忽略（可能是舊圖的快取）。QA 的 status 比頁面列表新，優先採用。
 * page.compose_pending（後端算）：選定圖不是目前背景＋角色動作＋位置的合成圖 → 構圖 warn「待更新」。
 * 完成一項和 B 說法一致：還在重新合成（recompose_pending、沒失敗）→ todo「製作中」；請看一下（attention，
 * 包括重新合成失敗）→ warn「請看一下」。
 */
export function checklist(
  page: Page,
  qa: QA | undefined,
  opts: { selectedKind?: Generation["kind"] } = {},
): CheckItem[] {
  const selected = page.selected_generation_id != null;
  const q = selected ? qa : undefined;

  let compose: CheckItem = { key: "compose", label: "構圖", state: "todo", note: "" };
  if (selected && page.compose_pending) {
    // 選定圖不是用目前背景＋角色動作＋位置合成的（改了位置，或舊流程頁面選了新素材）；不影響完成狀態
    compose = { ...compose, state: "warn", note: "待更新" };
  } else if (selected) {
    compose = { ...compose, state: "done", note: opts.selectedKind === "page" ? "舊流程圖片" : "已合成" };
  } else if (page.background_generation_id == null) {
    compose = { ...compose, note: "還沒選背景" };
  } else if (page.pose_generation_id == null) {
    compose = { ...compose, note: "還沒選角色動作" };
  }

  let line: CheckItem = { key: "line", label: "補線", state: "todo", note: "" };
  if (selected) {
    if (!q) line = { ...line, note: "檢查中…" };
    else if (q.gap_count > 0) line = { ...line, state: "warn", note: `${q.gap_count} 個缺口` };
    else line = { ...line, state: "done", note: "線條都接好了" };
  }

  const status = q?.status ?? page.status;
  let passed: CheckItem = { key: "passed", label: "完成", state: "todo", note: "" };
  if (recomposeRunning(page)) {
    passed = { ...passed, note: STATE_LABEL.working };
  } else if (page.attention) {
    passed = { ...passed, state: "warn", note: STATE_LABEL.attention };
  } else if (selected && status === "passed") {
    const gaps = q?.forced_pass_reason && q.gap_count > 0 ? q.gap_count : 0;
    passed = { ...passed, state: "done", note: gaps > 0 ? `完成（${gaps} 個缺口）` : "完成" };
  }

  return [compose, line, passed];
}
