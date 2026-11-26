"use client";

import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { copyText } from "@/lib/copyText";
import { errMsg } from "@/features/editing/errMsg";
import { MAX_ACTION, MAX_CAPTION_EN, MAX_CAPTION_ZH, MAX_SCENE } from "@/lib/limits";
import { noCharacterScene } from "@/lib/noCharacterScene";
import { UPLOAD_ACCEPT, UPLOAD_HINT } from "@/lib/upload";
import { AI_PROMPT } from "./aiPrompt";
import type { DraftPage, PreviewEntry, StoryPreview } from "./storyPreview";
import type { SetupPagePatch } from "./useUpdatePageFromSetup";
import { CopyPromptDialog } from "./CopyPromptDialog";
import { ThemePackDialog } from "./ThemePackDialog";
import type { ThemePack } from "@/lib/themePacks";
import styles from "./SetupPage.module.css";

export interface StorySectionProps {
  preview: StoryPreview;
  text: string;
  onText: (t: string) => void;
  /** 套用了主題包（開始做時，貼上框的文字還是這個主題包的，就用它的線條設定）。 */
  onPack?: (pack: ThemePack) => void;
  /** 選了上傳背景的頁（PreviewEntry.key → 檔案）：按「開始做這本書」才上傳。 */
  uploads: ReadonlyMap<string, File>;
  onUpload: (key: string, file: File | null) => void;
  /** 「＋ 加一頁」：在最後多一頁空白頁（開始時才建立）。 */
  onAddPage: () => void;
  onDraftChange: (key: number, patch: Partial<Pick<DraftPage, "scene" | "action">>) => void;
  onDraftRemove: (key: number) => void;
  /** 已有的頁按「儲存」：只送改過的欄位；失敗要 reject（列留在編輯狀態）。 */
  onSavePage: (pageId: number, patch: SetupPagePatch) => Promise<void>;
  /** 這一頁放不放主角（勾「不放主角」＝false）。 */
  onCharacter?: (key: string, include: boolean) => void;
  /** 總開關：true＝每一頁（沒鎖的）都不放主角。 */
  onCharacterAll?: (noCharacter: boolean) => void;
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
  onPack,
  uploads,
  onUpload,
  onAddPage,
  onDraftChange,
  onDraftRemove,
  onSavePage,
  onCharacter,
  onCharacterAll,
  existing,
  disabled,
}: StorySectionProps) {
  const toast = useToast();
  const [packOpen, setPackOpen] = useState(false);
  const { entries, problems, pasted } = preview;
  // 總開關：只看沒鎖的頁（相框頁一定放主角）
  const unlocked = entries.filter((e) => !e.characterLocked);
  const noneCount = unlocked.filter((e) => !e.includeCharacter).length;
  const allNone = unlocked.length > 0 && noneCount === unlocked.length;
  const mixed = noneCount > 0 && !allNone;
  const masterRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (masterRef.current) masterRef.current.indeterminate = mixed;
  }, [mixed]);

  const [promptOpen, setPromptOpen] = useState(false);
  const prompt = AI_PROMPT(pasted || existing || 10);
  const copyPrompt = async () => {
    if (await copyText(prompt)) toast.show("已複製，貼到 ChatGPT 或 Claude，再接上你的故事", "success");
    else setPromptOpen(true); // 自動複製不行：開出全選好的指令，讓人自己複製
  };

  const count =
    pasted > 0
      ? `讀到 ${pasted} 段${problems.length > 0 ? `，${problems.length} 段不會匯入` : ""}${existing > 0 ? `（這本書已經有 ${existing} 頁：空的欄位才會填入）` : ""}`
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
      {entries.length > 0 && (
        <label className={styles.noChar}>
          <input
            ref={masterRef}
            type="checkbox"
            checked={allNone}
            disabled={disabled || unlocked.length === 0}
            onChange={(ev) => onCharacterAll?.(ev.target.checked)}
          />
          不放主角
        </label>
      )}
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
              onSavePage={onSavePage}
              onCharacter={(include) => onCharacter?.(e.key, include)}
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
          onPack?.(pack);
        }}
      />
      <CopyPromptDialog open={promptOpen} text={prompt} onClose={() => setPromptOpen(false)} />
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
  onSavePage,
  onCharacter,
}: {
  entry: PreviewEntry;
  onCharacter: (include: boolean) => void;
  file: File | null;
  disabled: boolean;
  onFile: (f: File | null) => void;
  onDraftChange: (patch: Partial<Pick<DraftPage, "scene" | "action">>) => void;
  onDraftRemove: () => void;
  onSavePage: (pageId: number, patch: SetupPagePatch) => Promise<void>;
}) {
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [vals, setVals] = useState({ scene: "", action: "", zh: "", en: "" });
  const existingPage = e.pageId != null && e.draft == null;
  const startEdit = () => {
    setVals({ scene: e.scene, action: e.action, zh: e.captionZh, en: e.captionEn });
    setEditing(true);
  };
  const save = async () => {
    const patch: SetupPagePatch = {};
    if (vals.scene !== e.scene) patch.scene_prompt = vals.scene;
    if (vals.action !== e.action) patch.action_prompt = vals.action;
    if (vals.zh !== e.captionZh) patch.caption_zh = vals.zh;
    if (vals.en !== e.captionEn) patch.caption_en = vals.en;
    if (Object.keys(patch).length === 0) return setEditing(false);
    setSaving(true);
    try {
      await onSavePage(e.pageId!, patch);
      setEditing(false);
    } catch (err) {
      toast.show(errMsg(err, "沒辦法儲存這一頁"), "error");
    } finally {
      setSaving(false);
    }
  };
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
        ) : editing ? (
          <div className={styles.draft}>
            <input
              type="text"
              className={`${styles.input} ${styles.draftInput}`}
              aria-label={`第 ${e.n} 頁場景`}
              maxLength={MAX_SCENE}
              value={vals.scene}
              disabled={saving || disabled}
              onChange={(ev) => setVals({ ...vals, scene: ev.target.value })}
            />
            <input
              type="text"
              className={`${styles.input} ${styles.draftInput}`}
              aria-label={`第 ${e.n} 頁動作`}
              maxLength={MAX_ACTION}
              value={vals.action}
              disabled={saving || disabled}
              onChange={(ev) => setVals({ ...vals, action: ev.target.value })}
            />
            {e.materials.backgroundUploaded && (
              <span className={`${styles.hint} ${styles.fullRow}`}>背景是你上傳的圖，不會重畫</span>
            )}
            <input
              type="text"
              className={`${styles.input} ${styles.draftInput}`}
              aria-label={`第 ${e.n} 頁中文`}
              placeholder="中文"
              maxLength={MAX_CAPTION_ZH}
              value={vals.zh}
              disabled={saving || disabled}
              onChange={(ev) => setVals({ ...vals, zh: ev.target.value })}
            />
            <input
              type="text"
              className={`${styles.input} ${styles.draftInput}`}
              aria-label={`第 ${e.n} 頁英文`}
              placeholder="英文"
              maxLength={MAX_CAPTION_EN}
              value={vals.en}
              disabled={saving || disabled}
              onChange={(ev) => setVals({ ...vals, en: ev.target.value })}
            />
            <div className={`${styles.editActions} ${styles.fullRow}`}>
              <Button disabled={saving || disabled} onClick={() => void save()}>
                儲存
              </Button>
              <Button disabled={saving} onClick={() => setEditing(false)}>
                取消
              </Button>
            </div>
          </div>
        ) : (
          <span className={styles.rowText}>
            {scene}・{action}
          </span>
        )}
        {!e.includeCharacter && !uploadOn && e.scene.trim() !== "" && (
          <span className={styles.hint}>會畫成：{noCharacterScene(e.scene)}</span>
        )}
        {(e.materials.redrawBackground || e.materials.redrawPose || e.captionZh || e.captionEn || e.materials.hasPose || (e.materials.hasBackground && !uploadOn)) && (
          <span className={styles.chips}>
            {(e.materials.redrawBackground || e.materials.redrawPose) && <span className={styles.chip}>要重畫</span>}
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
        <span className={styles.noCharCell}>
          <label className={styles.noChar}>
            <input
              type="checkbox"
              checked={!e.includeCharacter}
              disabled={disabled || e.characterLocked}
              aria-describedby={e.characterLocked ? `frame-note-${e.key}` : undefined}
              onChange={(ev) => onCharacter(!ev.target.checked)}
            />
            不放主角
          </label>
          {e.characterLocked && (
            <span id={`frame-note-${e.key}`} className={styles.hint}>
              相框頁要放主角
            </span>
          )}
        </span>
        {existingPage && !editing && (
          <Button disabled={disabled} onClick={startEdit}>
            編輯
          </Button>
        )}
        {draft && (
          <button type="button" className={styles.remove} aria-label={`刪掉第 ${e.n} 頁`} disabled={disabled} onClick={onDraftRemove}>
            ✕
          </button>
        )}
      </div>
    </li>
  );
}
