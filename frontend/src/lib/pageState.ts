import type { Page } from "@/api/client";

/** 真的有問題要處理的 attention（例如 "failed"）；軟標記 review_pose（換了新的主角動作）不算。 */
export const hardAttention = (p: { attention?: string | null }) => p.attention != null && p.attention !== "review_pose";

/** 頁面流程還在重新合成這頁（C 改過、還沒做完）；失敗了（hardAttention）就不是，要到 C 按「再試一次」。 */
export const recomposeRunning = (p: Page | undefined) => !!p?.recompose_pending && !hardAttention(p);
