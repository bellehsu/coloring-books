import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Page } from "@/api/client";
import { editsBusyFor, flushAllEditsFor } from "@/features/editing/editsQueue";

/**
 * 先讓這頁選定圖的修線寫完（進階編輯與 C 兩個佇列都等）：改選背景／動作（合成圖會被清掉）或合成新的之後才到的 PUT
 * 會被後端以「選圖已更換」拒絕，出現多餘的「修線尚未儲存」。失敗已提示過，不擋後續動作。
 * flushing：正在等舊選圖修線寫入的頁（頁 id → 進行中的次數；那頁的縮圖暫時不能點）。
 */
export function useFlushSelectedEdits() {
  const qc = useQueryClient();
  const [flushing, setFlushing] = useState<ReadonlyMap<number, number>>(() => new Map());
  const bumpFlushing = (pageId: number, d: 1 | -1) =>
    setFlushing((m) => {
      const next = new Map(m);
      const n = (next.get(pageId) ?? 0) + d;
      if (n > 0) next.set(pageId, n);
      else next.delete(pageId);
      return next;
    });

  const flushSelectedEdits = async (page: Page) => {
    const sel = page.selected_generation_id;
    if (sel == null) return;
    if (!editsBusyFor(qc, page.id, sel)) return;
    bumpFlushing(page.id, 1);
    try {
      await flushAllEditsFor(qc, page.id, sel);
    } catch {
      /* 失敗已提示過 */
    } finally {
      bumpFlushing(page.id, -1);
    }
  };

  return { flushing, flushSelectedEdits };
}
