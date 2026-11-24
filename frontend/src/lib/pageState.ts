import type { Page } from "@/api/client";

/**
 * 軟標記：請你看一下，但這頁照樣可以印（不擋「可以印」、通過的頁仍算完成）。
 * review_pose＝批次重畫換了新的主角動作；review_frame＝肖像頁的背景找不到框，照一般擺放（後端 page_state.py）。
 */
const SOFT_ATTENTIONS: readonly string[] = ["review_pose", "review_frame"];

/** attention 是軟標記（review_pose、review_frame）。 */
export const softAttention = (p: { attention?: string | null }) => p.attention != null && SOFT_ATTENTIONS.includes(p.attention);

/** 真的有問題要處理的 attention（例如 "failed"）；軟標記（review_pose、review_frame）不算。 */
export const hardAttention = (p: { attention?: string | null }) => p.attention != null && !softAttention(p);

/** 頁面流程還在重新合成這頁（C 改過、還沒做完）；失敗了（hardAttention）就不是，要到 C 按「再試一次」。 */
export const recomposeRunning = (p: Page | undefined) => !!p?.recompose_pending && !hardAttention(p);

/** 真的完成的頁：通過、沒有要處理的問題、也沒在重新合成（軟標記仍算完成）。整本進度用。 */
export const pageDone = (p: Page) => p.status === "passed" && !hardAttention(p) && !recomposeRunning(p);
