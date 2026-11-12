import { useId, useState } from "react";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { ExclusiveChips } from "@/features/editing/ExclusiveChips";
import { errMsg } from "@/features/editing/errMsg";
import { usePageText } from "@/features/editing/usePageText";
import { ACTION_HINT, EXPRESSIONS, FACING_HINT, FACINGS } from "@/lib/actions";
import { MAX_ACTION } from "@/lib/limits";
import { useRegenerate } from "../fixApi";
import { quiet } from "@/lib/quiet";
import styles from "../FixPage.module.css";
import { Row, useSavePage } from "./Row";

export function PoseRow({
  bookId,
  page,
  hasCharacter,
  active,
  flushEdits,
  restore,
  busy,
  textKey,
  trackSave,
}: {
  bookId: number;
  page: Page;
  hasCharacter: boolean;
  active: boolean;
  flushEdits: () => Promise<void>;
  restore: (() => void) | null;
  busy: boolean;
  textKey: number;
  trackSave: (p: Promise<unknown>) => Promise<unknown>;
}) {
  const id = useId();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const savePage = useSavePage(bookId, page.id);
  const action = usePageText(page.action_prompt, (v) => savePage({ action_prompt: v }), textKey);
  const regen = useRegenerate(bookId, page.id);
  const trimmed = action.value.trim();

  const again = async () => {
    try {
      await action.save();
    } catch {
      return; // 存檔失敗已提示；不要用舊的動作畫
    }
    await flushEdits();
    await regen
      .mutateAsync({ kind: "pose", action: trimmed })
      .catch((e: unknown) => toast.show(errMsg(e, "無法換一張角色動作"), "error"));
  };

  const sub = active ? "正在畫新的一張…" : trimmed ? `動作：${trimmed}` : "還沒寫動作";
  return (
    <Row
      title="角色動作"
      tint="pink"
      sub={sub}
      restore={restore}
      actions={
        <>
          <Button
            disabled={!hasCharacter || !trimmed || active || regen.isPending || busy}
            onClick={() => void again()}
          >
            換一張
          </Button>
          <Button aria-expanded={open} aria-controls={`${id}-panel`} onClick={() => setOpen(!open)}>
            改動作
          </Button>
        </>
      }
    >
      {!hasCharacter && <p className={styles.rowNote}>這本書還沒有主角，先到「主角與故事」設定。</p>}
      {open && (
        <div id={`${id}-panel`} className={styles.panel}>
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
          <p className={styles.rowNote}>改好動作後按「換一張」。</p>
        </div>
      )}
    </Row>
  );
}
