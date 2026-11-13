import { useEffect, useId, useState } from "react";
import type { Generation, Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { useToast } from "@/components/ui/Toast";
import { ExclusiveChips } from "@/features/editing/ExclusiveChips";
import { errMsg } from "@/features/editing/errMsg";
import { usePageText } from "@/features/editing/usePageText";
import { ACTION_HINT, EXPRESSIONS, FACING_HINT, FACINGS } from "@/lib/actions";
import { MAX_ACTION } from "@/lib/limits";
import { useRegenerate } from "../fixApi";
import { isFreshPose, markSeenPose, readSeenPose } from "../seenPoses";
import { quiet } from "@/lib/quiet";
import styles from "../FixPage.module.css";
import { Row, useSavePage } from "./Row";

type Count = 1 | 2 | 3;
const COUNTS: { value: `${Count}`; label: string }[] = [
  { value: "1", label: "1" },
  { value: "2", label: "2" },
  { value: "3", label: "3" },
];

/** 這頁畫過的角色動作：成功、沒捨棄、有圖的，新的在前（src：去背圖，沒有就用原圖）。 */
function posesOf(gens: readonly Generation[], pageId: number) {
  return gens
    .filter((g) => g.kind === "pose" && g.page_id === pageId && g.status === "succeeded" && !g.discarded)
    .flatMap((g) => {
      const src = g.cutout_url ?? g.image_url;
      return src ? [{ g, src }] : [];
    })
    .sort((a, b) => b.g.id - a.g.id);
}

/**
 * 角色動作列（spec §4.4 #4B、mockups/2026-11-13/B_redraw_panel.png）：這頁畫過的角色動作排成一列，點一下就換上
 * （先存復原快照，↶ 復原回到上一張）；上次打開之後才畫好的標「新」（這個瀏覽器的 localStorage；
 * 第一次打開時比使用中的那張新的也算，例如批次重畫的候選）。
 * 下面一直看得到動作描述、表情、面向，「一次畫 1–3 張」與「重畫主角」：第 1 張好了換上去，其餘是候選。
 */
export function PoseRow({
  bookId,
  page,
  gens,
  hasCharacter,
  active,
  flushEdits,
  selectPose,
  restore,
  busy,
  textKey,
  trackSave,
}: {
  bookId: number;
  page: Page;
  /** 這頁的生成紀錄（FixPage 的 useGenerations）。 */
  gens: readonly Generation[];
  hasCharacter: boolean;
  active: boolean;
  flushEdits: () => Promise<void>;
  /** 換成畫過的另一張（useFixActions：先等文字、位置與筆畫寫完；進行中算 busy）。 */
  selectPose: (generationId: number) => Promise<unknown>;
  restore: (() => void) | null;
  busy: boolean;
  textKey: number;
  trackSave: (p: Promise<unknown>) => Promise<unknown>;
}) {
  const id = useId();
  const toast = useToast();
  const [count, setCount] = useState<Count>(1);
  const savePage = useSavePage(bookId, page.id);
  const action = usePageText(page.action_prompt, (v) => savePage({ action_prompt: v }), textKey);
  const regen = useRegenerate(bookId, page.id);
  const trimmed = action.value.trim();

  const poses = posesOf(gens, page.id);
  const maxId = poses[0]?.g.id ?? null;
  // 打開這頁時讀一次「上次看過的」與使用中的那張，之後記下最新的一張；畫面上的「新」留到下次打開
  const openSeen = () => ({ pageId: page.id, mark: readSeenPose(page.id), baseline: page.pose_generation_id });
  const [seen, setSeen] = useState(openSeen);
  if (seen.pageId !== page.id) setSeen(openSeen());
  const isFresh = (gid: number) =>
    seen.pageId === page.id && isFreshPose(gid, seen.mark, seen.baseline, page.pose_generation_id);
  useEffect(() => {
    if (maxId !== null) markSeenPose(page.id, maxId);
  }, [page.id, maxId]);

  const again = async () => {
    try {
      await action.save();
    } catch {
      return; // 存檔失敗已提示；不要用舊的動作畫
    }
    await flushEdits();
    await regen
      .mutateAsync({ kind: "pose", action: trimmed, count })
      .catch((e: unknown) => toast.show(errMsg(e, "無法重畫主角"), "error"));
  };

  const sub = active ? "正在畫新的一張…" : `這頁畫過 ${poses.length} 張・點一下就換`;
  return (
    <Row title="角色動作" tint="pink" sub={sub} restore={restore} actions={null}>
      {!hasCharacter && <p className={styles.rowNote}>這本書還沒有主角，先到「主角與故事」設定。</p>}
      {poses.length > 0 && (
        <ul className={styles.poses} aria-label="畫過的角色動作">
          {poses.map(({ g, src }, i) => {
            const on = g.id === page.pose_generation_id;
            const fresh = isFresh(g.id);
            return (
              <li key={g.id}>
                <button
                  type="button"
                  aria-pressed={on}
                  // 不用 disabled：換的時候焦點不會掉到 body
                  aria-disabled={busy && !on ? true : undefined}
                  className={styles.pose}
                  data-on={on}
                  onClick={on || busy ? undefined : () => void selectPose(g.id)}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={src}
                    alt={`角色動作第 ${poses.length - i} 張`}
                    className={styles.poseImg}
                    loading="lazy"
                  />
                  {on && <span className={`${styles.poseTag} ${styles.poseTagOn}`}>使用中</span>}
                  {fresh && <span className={`${styles.poseTag} ${styles.poseTagNew}`}>新</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className={styles.panel}>
        <label htmlFor={`${id}-action`} className={styles.panelLabel}>
          動作描述
        </label>
        <textarea
          id={`${id}-action`}
          rows={3}
          maxLength={MAX_ACTION}
          className={styles.textarea}
          value={action.value}
          aria-describedby={`${id}-hint`}
          onChange={(e) => action.setValue(e.target.value)}
          onBlur={() => quiet(trackSave(action.save()))}
        />
        <p id={`${id}-hint`} className={styles.rowNote}>
          {ACTION_HINT}
        </p>
        <ExclusiveChips
          label="表情"
          group={EXPRESSIONS}
          text={action.value}
          onChange={(v) => void quiet(trackSave(action.setNow(v)))}
        />
        <ExclusiveChips
          label="面向"
          group={FACINGS}
          text={action.value}
          onChange={(v) => void quiet(trackSave(action.setNow(v)))}
          hint={FACING_HINT}
        />
        <div className={styles.redraw}>
          <div className={styles.redrawCount}>
            <span>一次畫</span>
            <Segmented
              kind="radio"
              label="一次畫幾張"
              options={COUNTS}
              value={`${count}`}
              onChange={(v) => setCount(Number(v) as Count)}
            />
            <span>張</span>
          </div>
          <Button
            variant="primary"
            disabled={!hasCharacter || !trimmed || active || regen.isPending || busy}
            onClick={() => void again()}
          >
            重畫主角
          </Button>
        </div>
      </div>
    </Row>
  );
}
