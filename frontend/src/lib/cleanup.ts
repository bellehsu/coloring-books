/** 「清掉框外雜物」（spec 2026-11-24 §5.5）：按鈕、說明與縮圖小標的字，以及兩張背景卡片共用的判斷。 */

import type { Generation, Page } from "@/api/client";

export const CLEANUP_LABEL = "清掉框外雜物";
/** 不是肖像模式時按鈕旁的說明（和後端 409 同一句）。 */
export const CLEANUP_NOT_PORTRAIT = "有框的背景（肖像模式）才能清";
/** 縮圖上的小標：這張背景是清理產生的。 */
export const CLEANED_BADGE = "清過";

/** 肖像模式（主角放進框裡）：位置有 frame。只有這種頁能清。 */
export const isPortrait = (page: Pick<Page, "placement">) => page.placement?.frame != null;

/** 清理產生的背景（從另一張背景編輯出來的）。 */
export const isCleaned = (g: Pick<Generation, "source_generation_id">) => g.source_generation_id != null;
