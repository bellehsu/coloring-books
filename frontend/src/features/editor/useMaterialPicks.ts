import type { Generation, Page } from "@/api/client";
import { useSelectBackground, useSelectPose } from "@/api/hooks";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import type { AutoCompose } from "./autoCompose";
import type { ThumbKind } from "./Thumbs";
import { hasBoth } from "./useEditorAutoCompose";

export interface MaterialPicksArgs {
  bookId: number;
  current: Page | null;
  /** 正在等舊選圖修線寫入的頁（useFlushSelectedEdits）。 */
  flushing: ReadonlyMap<number, number>;
  flushSelectedEdits: (page: Page) => Promise<void>;
  autoCompose: AutoCompose;
}

/** 構圖右欄的素材選用（點背景／角色動作縮圖）。 */
export function useMaterialPicks({ bookId, current, flushing, flushSelectedEdits, autoCompose }: MaterialPicksArgs) {
  const toast = useToast();
  const selectBg = useSelectBackground(bookId);
  const selectPose = useSelectPose(bookId);
  const picking = selectBg.isPending || selectPose.isPending || flushing.has(current?.id ?? -1);

  /**
   * 縮圖選用：背景 → POST /background、角色動作 → POST /pose。合成圖只能經由合成產生。
   * 選好後兩個都有 → 自動合成。
   */
  const pickThumb = async (kind: ThumbKind, g: Generation) => {
    // 等修線寫入期間再點（連點）一律忽略，避免送出兩次
    if (!current || picking) return;
    const pageId = current.id;
    await flushSelectedEdits(current);
    const m = kind === "background" ? selectBg : selectPose;
    m.mutate(
      { pageId, generationId: g.id },
      {
        onSuccess: (page) => {
          if (hasBoth(page)) autoCompose.schedule(pageId);
        },
        onError: (e) => toast.show(errMsg(e, kind === "background" ? "無法選用這張背景" : "無法選用這個角色動作"), "error"),
      },
    );
  };

  return { picking, pickThumb };
}
