import type { Page } from "@/api/client";

/** 頁面流程還在重新合成這頁（C 改過、還沒做完）；失敗了（attention）就不是，要到 C 按「再試一次」。 */
export const recomposeRunning = (p: Page | undefined) => !!p?.recompose_pending && !p.attention;
