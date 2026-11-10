"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { qk, useCreatePage, usePages, useUpdatePage } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { parseStory, planImport, type ImportRow } from "@/lib/storyImport";
import { AI_PROMPT } from "./aiPrompt";
import styles from "./ImportDialog.module.css";

export interface ImportDialogProps {
  bookId: number;
  open: boolean;
  onClose: () => void;
}

function resultText(r: ImportRow): string {
  if (r.create) return "新增這一頁";
  if (r.fill.scene && r.fill.action) return "填入場景、動作";
  if (r.fill.scene) return "填入場景";
  if (r.fill.action) return "填入動作";
  return "已有內容，略過";
}

/** 貼上整本故事：預覽後匯入。只填空欄位、補不足的頁數，從不刪除頁面。 */
export function ImportDialog({ bookId, open, onClose }: ImportDialogProps) {
  const toast = useToast();
  const qc = useQueryClient();
  const pagesQ = usePages(bookId);
  const createPage = useCreatePage(bookId);
  const updatePage = useUpdatePage(bookId);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const existing = useMemo(
    () => [...(pagesQ.data ?? [])].sort((a, b) => a.position - b.position),
    [pagesQ.data],
  );
  const parsed = useMemo(() => parseStory(text), [text]);
  const rows = useMemo(() => planImport(parsed.pages, existing), [parsed.pages, existing]);

  const ok = rows.filter((r) => r.problems.length === 0);
  const maxCreateN = Math.max(existing.length, ...ok.filter((r) => r.create).map((r) => r.n));
  const createCount = maxCreateN - existing.length;
  const fillCells = ok.filter((r) => !r.create).reduce((s, r) => s + +r.fill.scene + +r.fill.action, 0);
  const skipCells = ok
    .filter((r) => !r.create)
    .reduce((s, r) => s + +(r.scene !== "" && !r.fill.scene) + +(r.action !== "" && !r.fill.action), 0);
  const bad = parsed.errors.length + rows.length - ok.length;
  const total = parsed.pages.length + parsed.errors.length;
  const nothing = createCount === 0 && fillCells === 0;

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(AI_PROMPT(total || existing.length || 1));
      toast.show("已複製", "success");
    } catch {
      toast.show("無法複製，請手動選取", "error");
    }
  };

  const apply = async () => {
    setBusy(true);
    try {
      // 1. 依序補頁（被跳過的中間段落補空白頁，維持頁序對應）
      const byN = new Map(ok.map((r) => [r.n, r]));
      for (let n = existing.length + 1; n <= maxCreateN; n++) {
        const r = byN.get(n);
        await createPage.mutateAsync({ scene_prompt: r?.scene ?? "", action_prompt: r?.action ?? "" });
      }
      // 2. 只填空欄位
      for (const r of ok) {
        if (r.create || (!r.fill.scene && !r.fill.action)) continue;
        await updatePage.mutateAsync({
          pageId: existing[r.n - 1].id,
          ...(r.fill.scene ? { scene_prompt: r.scene } : {}),
          ...(r.fill.action ? { action_prompt: r.action } : {}),
        });
      }
      await qc.invalidateQueries({ queryKey: qk.pages(bookId) });
      toast.show(`已匯入（新增 ${createCount} 頁、填入 ${fillCells} 格）`, "success");
      setText("");
      onClose();
    } catch (e) {
      await qc.invalidateQueries({ queryKey: qk.pages(bookId) });
      toast.show(`匯入失敗：${e instanceof Error ? e.message : "未知錯誤"}`, "error");
    } finally {
      setBusy(false);
    }
  };

  const errorRows = parsed.errors;
  return (
    <Dialog open={open} onClose={onClose} title="貼上整本故事">
      <div className={styles.body}>
        <p className={styles.hint}>
          把分鏡貼在下面（每頁寫「場景：」「動作：」，或從試算表貼兩欄）。已有內容的欄位不會被覆蓋，也不會刪除任何頁面。
        </p>
        <textarea
          className={styles.box}
          aria-label="貼上故事"
          rows={8}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={"第1頁\n場景：森林裡的小木屋\n動作：開心地揮手"}
        />
        <div className={styles.row}>
          <Button onClick={copyPrompt}>複製給 AI 的指令</Button>
        </div>
        {total > 0 && (
          <>
            <p className={styles.summary}>
              {`${total} 段：新增 ${createCount} 頁、填入 ${fillCells} 格、略過 ${skipCells} 格、${bad} 段看不懂`}
            </p>
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>頁</th>
                    <th>場景</th>
                    <th>動作</th>
                    <th>結果</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rows.map((r) => ({ n: r.n, row: r, err: null })), ...errorRows.map((e) => ({ n: e.n ?? 1e9, row: null, err: e }))]
                    .sort((a, b) => a.n - b.n)
                    .map((x) =>
                      x.row ? (
                        <tr key={`r${x.n}`}>
                          <td>{x.row.n}</td>
                          <td>{x.row.scene}</td>
                          <td>{x.row.action}</td>
                          <td className={x.row.problems.length ? styles.bad : undefined}>
                            {x.row.problems.length ? x.row.problems.join("；") : resultText(x.row)}
                          </td>
                        </tr>
                      ) : (
                        <tr key={`e${x.n}`}>
                          <td>{x.err!.n ?? ""}</td>
                          <td colSpan={2}>{x.err!.excerpt}</td>
                          <td className={styles.bad}>{x.err!.reason}</td>
                        </tr>
                      ),
                    )}
                </tbody>
              </table>
            </div>
          </>
        )}
        <div className={styles.row}>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={busy || nothing} onClick={apply}>
            {`匯入（新增 ${createCount} 頁、填入 ${fillCells} 格）`}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
