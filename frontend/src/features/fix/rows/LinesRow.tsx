import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Page, QA } from "@/api/client";
import { qk, useAutofix } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { peekEditsQueue } from "@/features/editing/editsQueue";
import { errMsg } from "@/features/editing/errMsg";
import type { StrokeOp } from "@/lib/strokes";
import { refreshFix } from "../fixApi";
import { QAError } from "../FixPreview";
import styles from "../FixPage.module.css";
import { Row } from "./Row";

export function LinesRow({
  bookId,
  page,
  qa,
  qaError,
  onRetryQA,
  blocked,
  tool,
  onTool,
  restore,
  busy,
}: {
  bookId: number;
  page: Page;
  qa: QA | undefined;
  qaError: string | null;
  onRetryQA: () => void;
  blocked: string | null;
  tool: StrokeOp | null;
  onTool: (t: StrokeOp) => void;
  restore: (() => void) | null;
  busy: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const autofix = useAutofix(bookId);
  const [fixing, setFixing] = useState(false);
  const gid = page.selected_generation_id;
  const noArt = gid == null;

  const runAutofix = async () => {
    if (gid == null || fixing) return;
    setFixing(true);
    try {
      const entry = peekEditsQueue(qc, page.id, gid, { oneclick: true });
      let added: number | null;
      if (entry?.autofix) {
        added = await entry.autofix(); // 畫布開著：走畫布的補線（先寫完畫面上的筆畫、補線期間延後寫入；失敗已提示）
      } else {
        await entry?.flush().catch(() => {});
        const res = await autofix.mutateAsync({ pageId: page.id, generationId: gid, oneclick: true });
        const { autofix_added, ...rest } = res;
        qc.setQueryData<QA>(qk.qa(page.id), rest as QA);
        added = autofix_added;
      }
      if (added == null) return;
      toast.show(added > 0 ? `接起來了 ${added} 處` : "沒有可以自動接起來的線了", added > 0 ? "success" : "info");
      void refreshFix(qc, bookId, page.id);
    } catch (e) {
      toast.show(errMsg(e, "自動補線失敗"), "error");
    } finally {
      setFixing(false);
    }
  };

  const gaps = qa?.gap_count;
  const sub = noArt
    ? "還沒有線稿"
    : gaps == null
      ? "線稿"
      : `${gaps > 0 ? `${gaps} 處沒接起來（可以不管）` : "線都接好了"}${page.has_user_strokes ? "・有你畫的線" : ""}`;
  const penOff = noArt || blocked !== null || busy;
  return (
    <Row
      title="線條"
      tint="yellow"
      sub={sub}
      restore={restore}
      actions={
        <>
          <Button disabled={noArt || fixing || busy || blocked !== null} onClick={() => void runAutofix()}>
            {fixing ? "補線中…" : "自動補線"}
          </Button>
          <Button variant={tool === "draw" ? "sky" : "secondary"} aria-pressed={tool === "draw"} disabled={penOff} onClick={() => onTool("draw")}>
            畫筆
          </Button>
          <Button variant={tool === "erase" ? "sky" : "secondary"} aria-pressed={tool === "erase"} disabled={penOff} onClick={() => onTool("erase")}>
            擦除
          </Button>
        </>
      }
    >
      {blocked && <p className={styles.rowWarn}>{blocked}</p>}
      {!noArt && qaError && <QAError message={qaError} onRetry={onRetryQA} alert />}
    </Row>
  );
}
