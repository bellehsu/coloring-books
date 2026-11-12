import { useCallback, useEffect, useRef, useState } from "react";
import type { Placement } from "@/api/client";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import { useSavePlacementC } from "./fixApi";

/** 方向鍵／＋－ 停下來多久才存（按住時鍵盤自動重複約每秒 30 次，放開後只存一次、只有一步復原）。 */
const KEY_SAVE_DELAY_MS = 400;

interface PlacementQueue {
  /** 等著送的（最新為準）。 */
  next: Placement | null;
  /** 按鍵停下來才送的。 */
  keyed: Placement | null;
  timer: ReturnType<typeof setTimeout> | null;
  /** 正在送的那一輪（一次只有一個 PUT 在路上）。 */
  run: Promise<void> | null;
}

/**
 * 拖完角色的位置存檔（PUT /placement?oneclick=true，每次後端都存快照、重新合成）：一次只送一個，送出中又改了
 * 只留最新的一筆，等前一個回來再送（latest-wins）；方向鍵／＋－ 停下來 400 ms 才存。存好前畫面用 draft。
 * saving 時復原／重做／還原停用，idle() 讓它們（與「這頁可以印了」）先等位置寫完（按鍵等著的立刻送），
 * 晚到的位置不會落在復原之後。
 */
export function usePlacementSaves(bookId: number, pageId: number) {
  const { show } = useToast();
  const { mutateAsync } = useSavePlacementC(bookId, pageId);
  const [draft, setDraft] = useState<Placement | null>(null);
  const [saving, setSaving] = useState(false);
  const q = useRef<PlacementQueue>({ next: null, keyed: null, timer: null, run: null });

  const kick = useCallback(
    function kick() {
      const s = q.current;
      if (s.run) return;
      s.run = (async () => {
        while (s.next) {
          const p = s.next;
          s.next = null;
          try {
            await mutateAsync(p);
          } catch (e) {
            show(errMsg(e, "位置沒有存起來，請再拖一次"), "error");
          }
        }
      })().finally(() => {
        s.run = null;
        if (s.next) kick();
        else if (!s.timer) {
          setDraft(null);
          setSaving(false);
        }
      });
    },
    [mutateAsync, show],
  );
  /** 按鍵等著的那一筆現在就送。 */
  const flushKey = useCallback(() => {
    const s = q.current;
    if (!s.timer) return;
    clearTimeout(s.timer);
    s.timer = null;
    s.next = s.keyed;
    s.keyed = null;
    kick();
  }, [kick]);
  // 卸載（換頁）時按鍵還沒送的也要送
  useEffect(() => flushKey, [flushKey]);

  const onChange = (p: Placement, via?: "key") => {
    const s = q.current;
    setDraft(p);
    setSaving(true);
    if (s.timer) clearTimeout(s.timer);
    s.timer = null;
    if (via === "key") {
      s.keyed = p;
      s.timer = setTimeout(flushKey, KEY_SAVE_DELAY_MS);
    } else {
      s.keyed = null;
      s.next = p;
      kick();
    }
  };
  const idle = async () => {
    flushKey();
    while (q.current.run) await q.current.run;
  };
  /** 還有位置沒存好（等著按鍵停、等著送、送出中）。 */
  const hasPending = useCallback(() => {
    const s = q.current;
    return s.timer != null || s.next != null || s.run != null;
  }, []);
  return { draft, saving, save: onChange, idle, hasPending };
}

/** usePlacementSaves 的回傳（預覽拖角色、useFixActions 等位置寫完用）。 */
export type PlacementSaves = ReturnType<typeof usePlacementSaves>;
