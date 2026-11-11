"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { AI_PROMPT } from "@/features/story/aiPrompt";
import { ThemePackDialog } from "@/features/story/ThemePackDialog";
import { MAX_ACTION, MAX_SCENE } from "@/lib/limits";
import { UPLOAD_ACCEPT, UPLOAD_HINT } from "@/lib/upload";
import type { DraftPage, PreviewEntry, StoryPreview } from "./storyPreview";
import styles from "./SetupPage.module.css";

export interface StorySectionProps {
  preview: StoryPreview;
  text: string;
  onText: (t: string) => void;
  /** 選了上傳背景的頁（PreviewEntry.key → 檔案）：按「開始做這本書」才上傳。 */
  uploads: ReadonlyMap<string, File>;
  onUpload: (key: string, file: File | null) => void;
  /** 「＋ 加一頁」：在最後多一頁空白頁（開始時才建立）。 */
  onAddPage: () => void;
  onDraftChange: (key: number, patch: Partial<Pick<DraftPage, "scene" | "action">>) => void;
  onDraftRemove: (key: number) => void;
  /** 這本書已經有幾頁。 */
  existing: number;
  disabled: boolean;
}

/**
 * ③ 故事：貼上（或套用主題包）→ 即時預覽每一頁（頁碼、場景・動作、圖下方的字），每頁可以選
 * 「AI 畫背景／↑ 上傳背景」。現有的頁一起列出；貼上的故事只填空欄位、補不足的頁（同「貼上整本故事」）。
 */
export function StorySection({
  preview,
  text,
  onText,
  uploads,
  onUpload,
  onAddPage,
  onDraftChange,
  onDraftRemove,
  existing,
  disabled,
}: StorySectionProps) {
  const toast = useToast();
  const [packOpen, setPackOpen] = useState(false);
  const { entries, problems, pasted } = preview;

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(AI_PROMPT(pasted || existing || 10));
      toast.show("已複製，貼到 ChatGPT 或 Claude，再接上你的故事", "success");
    } catch {
      toast.show("無法複製，請手動選取", "error");
    }
  };

  const count =
    pasted > 0
      ? `讀到 ${pasted} 段${existing > 0 ? `（這本書已經有 ${existing} 頁：空的欄位才會填入）` : ""}`
      : existing > 0
        ? `這本書有 ${existing} 頁。要加頁就把故事貼在上面。`
        : null;

  return (
    <section aria-labelledby="setup-s3" className={styles.card}>
      <div className={styles.cardHead}>
        <span className={`${styles.num} ${styles.numGreen}`} aria-hidden="true">
          3
        </span>
        <h2 id="setup-s3" className={styles.h2}>
          故事
        </h2>
        <div className={styles.headActions}>
          <Button className={styles.packBtn} disabled={disabled} onClick={() => setPackOpen(true)}>
            套用主題包
          </Button>
          <Button disabled={disabled} onClick={() => void copyPrompt()}>
            複製給 AI 的指令
          </Button>
        </div>
      </div>
      <label className={styles.field}>
        <span className={styles.label}>把故事貼在這裡</span>
        <textarea
          className={`${styles.input} ${styles.story}`}
          rows={6}
          value={text}
          disabled={disabled}
          onChange={(e) => onText(e.target.value)}
          placeholder={"第1頁\n場景：消防局門口，車庫門關著\n動作：開著紅色消防車，從車窗探出頭揮手\n中文：消防車　英文：Fire Engine"}
        />
      </label>
      <p className={styles.hint}>
        每頁寫「場景：」「動作：」，圖下方的字用「中文：」「英文：」（選填）。也可以從試算表貼上。
      </p>

      {count && <p className={styles.count}>{count}</p>}
      {entries.length > 0 && (
        <ol aria-label="每一頁" className={styles.rows}>
          {entries.map((e) => (
            <StoryPreviewRow
              key={e.key}
              entry={e}
              file={uploads.get(e.key) ?? null}
              disabled={disabled}
              onFile={(f) => onUpload(e.key, f)}
              onDraftChange={(patch) => e.draft != null && onDraftChange(e.draft, patch)}
              onDraftRemove={() => e.draft != null && onDraftRemove(e.draft)}
            />
          ))}
        </ol>
      )}
      <div>
        <Button disabled={disabled} onClick={onAddPage}>
          ＋ 加一頁
        </Button>
      </div>
      {entries.length > 0 && <p className={styles.hint}>上傳背景：{UPLOAD_HINT}</p>}
      {problems.length > 0 && (
        <div role="note" aria-label="不會匯入的段落" className={styles.problems}>
          <b>這些段落不會匯入：</b>
          <ul>
            {problems.map((p, i) => (
              <li key={`${p.n ?? "x"}-${i}`}>
                {p.n != null ? `第 ${p.n} 段：` : ""}
                {p.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ThemePackDialog
        open={packOpen}
        onClose={() => setPackOpen(false)}
        onChoose={(pack) => {
          setPackOpen(false);
          onText(pack.text);
        }}
      />
    </section>
  );
}

function StoryPreviewRow({
  entry: e,
  file,
  disabled,
  onFile,
  onDraftChange,
  onDraftRemove,
}: {
  entry: PreviewEntry;
  file: File | null;
  disabled: boolean;
  onFile: (f: File | null) => void;
  onDraftChange: (patch: Partial<Pick<DraftPage, "scene" | "action">>) => void;
  onDraftRemove: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const uploadedBefore = file == null && e.materials.backgroundUploaded;
  const uploadOn = file != null || uploadedBefore;
  const draft = e.draft != null;
  const scene = file
    ? `（我上傳的 ${file.name}）`
    : uploadedBefore
      ? "（我上傳的圖片）"
      : e.scene || "（還沒寫場景）";
  const action = e.action || "（沒有動作）";
  return (
    <li className={styles.row} aria-label={`第 ${e.n} 頁`}>
      <span className={styles.rowNo} aria-hidden="true">
        {e.n}
      </span>
      <div className={styles.rowBody}>
        {draft ? (
          // 「＋ 加一頁」的頁：直接在這裡寫場景和動作
          <div className={styles.draft}>
            <input
              type="text"
              className={`${styles.input} ${styles.draftInput}`}
              aria-label={`第 ${e.n} 頁場景`}
              placeholder="場景：地點和物品，不要寫人物"
              maxLength={MAX_SCENE}
              value={e.scene}
              disabled={disabled}
              onChange={(ev) => onDraftChange({ scene: ev.target.value })}
            />
            <input
              type="text"
              className={`${styles.input} ${styles.draftInput}`}
              aria-label={`第 ${e.n} 頁動作`}
              placeholder="動作：主角在做什麼"
              maxLength={MAX_ACTION}
              value={e.action}
              disabled={disabled}
              onChange={(ev) => onDraftChange({ action: ev.target.value })}
            />
            {file && <span className={styles.rowText}>（我上傳的 {file.name}）</span>}
          </div>
        ) : (
          <span className={styles.rowText}>
            {scene}・{action}
          </span>
        )}
        {(e.captionZh || e.captionEn || e.materials.hasPose || (e.materials.hasBackground && !uploadOn)) && (
          <span className={styles.chips}>
            {e.captionZh && <span className={styles.chip}>中文：{e.captionZh}</span>}
            {e.captionEn && <span className={styles.chip}>英文：{e.captionEn}</span>}
            {e.materials.hasBackground && !uploadOn && <span className={styles.chipDone}>已有背景</span>}
            {e.materials.hasPose && <span className={styles.chipDone}>已有動作</span>}
          </span>
        )}
      </div>
      <div className={styles.rowEnd}>
        <div role="group" aria-label={`第 ${e.n} 頁的背景`} className={styles.toggle}>
          <button
            type="button"
            className={styles.toggleItem}
            aria-pressed={!uploadOn}
            // 之前上傳的背景已經選用：這裡改不回 AI（到那一頁的「AI 再畫一張」）
            disabled={disabled || uploadedBefore}
            onClick={() => onFile(null)}
          >
            AI 畫背景
          </button>
          <button
            type="button"
            className={styles.toggleItem}
            aria-pressed={uploadOn}
            disabled={disabled}
            onClick={() => input.current?.click()}
          >
            <span aria-hidden="true">↑</span> 上傳背景
          </button>
          {/* 鍵盤與報讀用上面的按鈕；檔案選擇本身不進 tab 順序 */}
          <input
            ref={input}
            type="file"
            accept={UPLOAD_ACCEPT}
            aria-label={`第 ${e.n} 頁上傳背景`}
            tabIndex={-1}
            className={styles.file}
            disabled={disabled}
            onChange={(ev) => {
              const f = ev.target.files?.[0];
              ev.target.value = "";
              if (f) onFile(f);
            }}
          />
        </div>
        {draft && (
          <button type="button" className={styles.remove} aria-label={`刪掉第 ${e.n} 頁`} disabled={disabled} onClick={onDraftRemove}>
            ✕
          </button>
        )}
      </div>
    </li>
  );
}
