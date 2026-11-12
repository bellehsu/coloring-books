import { useId, useState, type ChangeEvent } from "react";
import type { Generation, Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import { usePageText } from "@/features/editing/usePageText";
import { MAX_SCENE } from "@/lib/limits";
import { UPLOAD_ACCEPT } from "@/lib/upload";
import { useRegenerate, useUploadBackgroundC } from "../fixApi";
import { showRestore } from "../fixState";
import styles from "../FixPage.module.css";
import { quiet, Row, useSavePage } from "./Row";

export function BackgroundRow({
  bookId,
  page,
  bgGen,
  active,
  flushEdits,
  restore,
  busy,
  textKey,
  trackSave,
}: {
  bookId: number;
  page: Page;
  bgGen: Generation | undefined;
  active: boolean;
  flushEdits: () => Promise<void>;
  restore: (() => void) | null;
  busy: boolean;
  textKey: number;
  trackSave: (p: Promise<unknown>) => Promise<unknown>;
}) {
  const id = useId();
  const toast = useToast();
  const regen = useRegenerate(bookId, page.id);
  const upload = useUploadBackgroundC(bookId, page.id);
  const [uploading, setUploading] = useState(false);
  const [open, setOpen] = useState(false);
  const savePage = useSavePage(bookId, page.id);
  const scene = usePageText(page.scene_prompt, (v) => savePage({ scene_prompt: v }), textKey);
  const sceneText = scene.value.trim();

  const again = async () => {
    try {
      await scene.save(); // 剛改的場景先存好，新的背景才會用它
    } catch {
      return; // 存檔失敗已提示；不要用舊的場景畫
    }
    await flushEdits();
    await regen.mutateAsync({ kind: "background" }).catch((e: unknown) => toast.show(errMsg(e, "無法再畫一張背景"), "error"));
  };
  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同一個檔案再選一次也會觸發
    if (!file || uploading) return;
    setUploading(true);
    void (async () => {
      try {
        await flushEdits();
        await upload.mutateAsync(file);
      } catch (err) {
        toast.show(errMsg(err, "無法上傳背景圖"), "error");
      } finally {
        setUploading(false);
      }
    })();
  };

  const source = active
    ? "正在畫新的一張…"
    : uploading
      ? "上傳中…"
      : page.background_generation_id == null
        ? "還沒有背景"
        : bgGen?.source === "upload"
          ? "你上傳的圖片"
          : showRestore(page, "background")
            ? "你換過"
            : "AI 畫的";
  const uploaded = !active && !uploading && bgGen?.source === "upload";
  const sub = sceneText && !uploaded ? `${source}・場景：${sceneText}` : source;
  const uploadOff = uploading || busy;
  return (
    <Row
      title="背景"
      tint="sky"
      sub={sub}
      restore={restore}
      actions={
        <>
          <Button disabled={active || regen.isPending || busy} onClick={() => void again()}>
            AI 再畫一張
          </Button>
          <label className={styles.upload} data-disabled={uploadOff}>
            <span aria-hidden="true">↑</span> 上傳圖片
            <input type="file" accept={UPLOAD_ACCEPT} className={styles.file} disabled={uploadOff} onChange={onFile} />
          </label>
          <Button aria-expanded={open} aria-controls={`${id}-panel`} onClick={() => setOpen(!open)}>
            改場景
          </Button>
        </>
      }
    >
      {open && (
        <div id={`${id}-panel`} className={styles.panel}>
          <label htmlFor={`${id}-scene`} className={styles.panelLabel}>
            場景描述
          </label>
          <textarea
            id={`${id}-scene`}
            rows={3}
            maxLength={MAX_SCENE}
            className={styles.textarea}
            value={scene.value}
            onChange={(e) => scene.setValue(e.target.value)}
            onBlur={() => quiet(trackSave(scene.save()))}
          />
          <p className={styles.rowNote}>只寫地點和物品，不要寫人物或動物。改好場景後按「AI 再畫一張」。</p>
        </div>
      )}
    </Row>
  );
}
