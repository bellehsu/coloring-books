"use client";

import { useEffect, useRef, useState } from "react";
import type { Generation, Page } from "@/api/client";
import { Icons } from "@/components/ui/Icons";
import { Segmented } from "@/components/ui/Segmented";
import { MAX_ACTION, MAX_SCENE } from "@/lib/limits";
import { UPLOAD_ACCEPT, UPLOAD_HINT } from "@/lib/upload";
import styles from "./StoryPage.module.css";

export type BgMode = "ai" | "upload";
export const ACTION_CHIPS = ["站著", "揮手", "跳起來", "拿著東西", "看書"] as const;
const DEBOUNCE_MS = 600;

/** 縮圖格的狀態：圖、排隊中、生成中 N%、失敗（可重試）或還沒有。 */
export type SlotView =
  | { kind: "image"; url: string }
  | { kind: "text"; text: string }
  | { kind: "retry"; error: string | null }
  | { kind: "empty" };

const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

/** gens：同一頁、同一種類（背景或角色動作）的生成紀錄；selectedId：頁面已選用的那一筆。 */
export function slotView(gens: readonly Generation[], selectedId: number | null): SlotView {
  const live = gens.filter((g) => !g.discarded);
  const selected = selectedId != null ? gens.find((g) => g.id === selectedId) : undefined;
  if (selected?.image_url) return { kind: "image", url: selected.image_url };
  const latest = [...live].sort((a, b) => b.id - a.id);
  const active = latest.find(isActive);
  if (active) {
    if (active.status === "running" || active.queue_position === 0) {
      const p = active.progress;
      return { kind: "text", text: p && p[1] > 0 ? `生成中 ${Math.round((p[0] / p[1]) * 100)}%` : "生成中" };
    }
    return { kind: "text", text: "排隊中" };
  }
  const done = latest.find((g) => g.status === "succeeded" && g.image_url);
  if (done?.image_url) return { kind: "image", url: done.image_url };
  const failed = latest.find((g) => g.status === "failed");
  if (failed) return { kind: "retry", error: failed.error };
  return { kind: "empty" };
}

/**
 * 輸入中的文字：停手 600 ms 或離開欄位就存；伺服器值追上之後回到顯示伺服器值。
 * 卸載（離開故事頁）時還在等的輸入馬上存。
 */
function useAutosave(server: string, save: (v: string) => Promise<unknown>) {
  const [draft, setDraft] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 送出中的文字：避免同一段文字在回應前又送一次（例如停手存檔後馬上離開欄位）。送完就清掉。 */
  const sent = useRef<string | null>(null);
  /** 還在等 debounce 的文字。 */
  const waiting = useRef<string | null>(null);
  const saveRef = useRef(save);
  const serverRef = useRef(server);
  useEffect(() => {
    saveRef.current = save;
    serverRef.current = server;
  });
  useEffect(() => {
    if (draft !== null && draft === server && !timer.current) setDraft(null);
  }, [draft, server]);

  // 只用 ref，卸載時的 cleanup 也能呼叫
  const flushRef = useRef((v: string) => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    waiting.current = null;
    if (v === serverRef.current || v === sent.current) return;
    sent.current = v;
    const done = () => {
      if (sent.current === v) sent.current = null; // 送完（成功或失敗）：同一段文字之後還能再送
    };
    saveRef.current(v).then(done, done);
  });
  const flush = (v: string) => flushRef.current(v);
  useEffect(() => {
    const flushNow = flushRef.current;
    return () => {
      if (waiting.current !== null) flushNow(waiting.current);
    };
  }, []);
  return {
    value: draft ?? server,
    onChange(v: string) {
      setDraft(v);
      waiting.current = v;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        flush(v);
      }, DEBOUNCE_MS);
    },
    onBlur: () => {
      if (draft !== null) flush(draft);
    },
    setNow(v: string) {
      setDraft(v);
      flush(v);
    },
  };
}

export interface StoryRowProps {
  n: number;
  page: Page;
  mode: BgMode;
  onModeChange: (m: BgMode) => void;
  /** 這個畫面剛上傳的檔名；沒有時（重新整理後）顯示「已上傳圖片」。 */
  fileName?: string;
  /** 頁面選用的背景是上傳的圖片（AI 產生的背景不算）。 */
  uploaded?: boolean;
  uploading?: boolean;
  onUpload: (file: File) => void;
  /** 回傳的 Promise 失敗（呼叫端已提示）時，同一段文字之後可以再送。 */
  onSave: (patch: { scene_prompt?: string; action_prompt?: string }) => Promise<unknown>;
  bg: SlotView;
  pose: SlotView;
  onRetry: () => void;
  /** 動作欄位的說明（「只寫動作，地點寫在背景…」）元素 id；故事頁只顯示一次，每一列的動作欄位都連到它。 */
  actionHintId?: string;
}

export function StoryRow({ n, page, mode, onModeChange, fileName, uploaded = false, uploading, onUpload, onSave, bg, pose, onRetry, actionHintId }: StoryRowProps) {
  const scene = useAutosave(page.scene_prompt, (v) => onSave({ scene_prompt: v }));
  const action = useAutosave(page.action_prompt, (v) => onSave({ action_prompt: v }));
  const fileInput = useRef<HTMLInputElement>(null);
  const hasUpload = !!fileName || uploaded;
  /** 常用動作：欄位空的時候填入；已有文字就用「、」接在後面（超過字數就不加）。 */
  const addChip = (c: string) => {
    const cur = action.value.trim();
    const next = cur ? `${cur}、${c}` : c;
    if (next.length <= MAX_ACTION) action.setNow(next);
  };

  return (
    <article className={styles.row} aria-label={`第 ${n} 頁`}>
      <div className={styles.num} aria-hidden="true">
        {n}
      </div>
      <div className={styles.field}>
        <div className={styles.fieldHead}>
          <span className={styles.lbl}>背景</span>
          <Segmented<BgMode>
            kind="radio"
            label={`第 ${n} 頁背景來源`}
            value={mode}
            onChange={onModeChange}
            options={[
              { value: "ai", label: "AI 產生" },
              { value: "upload", label: "上傳圖片" },
            ]}
          />
        </div>
        {mode === "ai" ? (
          <textarea
            rows={2}
            className={styles.textarea}
            aria-label={`第 ${n} 頁場景`}
            maxLength={MAX_SCENE}
            value={scene.value}
            onChange={(e) => scene.onChange(e.target.value)}
            onBlur={scene.onBlur}
          />
        ) : (
          <>
            <div className={styles.upl}>
              <Icons.upload size={20} />
              <span className={styles.fileName}>
                {uploading
                  ? "上傳中…"
                  : hasUpload
                    ? `${fileName ?? "已上傳圖片"}（已裁成頁面比例）`
                    : "選一張圖片當這頁的背景"}
              </span>
              <input
                ref={fileInput}
                type="file"
                accept={UPLOAD_ACCEPT}
                hidden
                aria-label={`第 ${n} 頁背景圖片`}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) onUpload(f);
                }}
              />
              <button type="button" className={styles.chip} disabled={uploading} onClick={() => fileInput.current?.click()}>
                {hasUpload ? "換一張" : "選擇圖片"}
              </button>
            </div>
            <span className={styles.hint}>{UPLOAD_HINT}</span>
          </>
        )}
      </div>
      <div className={styles.field}>
        <span className={styles.lbl}>角色在做什麼</span>
        <textarea
          rows={2}
          className={styles.textarea}
          aria-label={`第 ${n} 頁角色動作`}
          aria-describedby={actionHintId}
          maxLength={MAX_ACTION}
          value={action.value}
          onChange={(e) => action.onChange(e.target.value)}
          onBlur={action.onBlur}
        />
        <div className={styles.chips}>
          {ACTION_CHIPS.map((c) => (
            <button key={c} type="button" className={styles.chip} onClick={() => addChip(c)}>
              {c}
            </button>
          ))}
        </div>
      </div>
      <div className={styles.thumbs}>
        <Thumb label="背景" alt={`第 ${n} 頁背景`} view={bg} onRetry={onRetry} />
        <Thumb label="角色動作" alt={`第 ${n} 頁角色動作`} view={pose} onRetry={onRetry} contain />
      </div>
    </article>
  );
}

function Thumb({ label, alt, view, onRetry, contain }: { label: string; alt: string; view: SlotView; onRetry: () => void; contain?: boolean }) {
  return (
    <div className={styles.th}>
      <div className={styles.box}>
        {view.kind === "image" && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={view.url} alt={alt} className={contain ? styles.contain : styles.cover} loading="lazy" />
        )}
        {view.kind === "text" && <span>{view.text}</span>}
        {view.kind === "retry" && (
          <button type="button" className={styles.retry} title={view.error ?? undefined} onClick={onRetry}>
            失敗，重試
          </button>
        )}
      </div>
      <span className={styles.hint}>{label}</span>
    </div>
  );
}
