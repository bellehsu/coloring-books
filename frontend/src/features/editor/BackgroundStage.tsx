"use client";

import { useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Book, Generation, Page } from "@/api/client";
import { guidesFromLayout } from "@/lib/raster";
import { uploadReference } from "./referenceSketch";
import { SketchCanvas } from "./SketchCanvas";
import { sketchSaverFor, type SketchSaver } from "./sketchSaver";
import styles from "./EditorPage.module.css";

export interface BackgroundStageProps {
  bookId: number;
  page: Page;
  layout: Book["layout"];
  /** 頁面選用的背景（page.background_generation_id 的 generation）；沒有或還在載入時 undefined。 */
  background: Generation | undefined;
}

/**
 * 背景模式的舞台。
 * Task 8 佔位：只放構圖草圖畫板（存檔、whenIdle、失敗重試沿用 sketchSaver，與 Phase 4 相同）。
 * Task 9 換成完整版：有選用背景時顯示背景圖，上方切換「背景圖／畫構圖」。
 */
export function BackgroundStage({ bookId, page, layout }: BackgroundStageProps) {
  const qc = useQueryClient();
  const sketchSaver = sketchSaverFor(qc);
  const pageId = page.id;
  const [gw, gh] = layout.generation_size;
  // 這個 session 存過（可能還在送、或失敗）→ 用最後存的內容；null＝空白。沒存過才用伺服器的 sketch_url。
  const pending = sketchSaver.latestUrl(pageId);
  const sketchSeed = pending !== undefined ? pending : page.sketch_url;
  return (
    <>
      <SketchCanvas
        key={pageId}
        width={gw}
        height={gh}
        guides={guidesFromLayout(layout)}
        initialUrl={sketchSeed}
        onSave={(blob) => sketchSaver.save(bookId, pageId, blob)}
        onUploadReference={(file) => uploadReference(bookId, file)}
        register={(h) => sketchSaver.register(bookId, pageId, h)}
      />
      <SketchSaveStatus saver={sketchSaver} pageId={pageId} />
    </>
  );
}

/** 草圖最後一次寫入失敗時，畫布下方持續顯示（toast 會消失）＋重試。 */
export function SketchSaveStatus({ saver, pageId }: { saver: SketchSaver; pageId: number }) {
  const failed = useSyncExternalStore(
    saver.subscribe,
    () => saver.failed(pageId),
    () => false,
  );
  if (!failed) return null;
  return (
    <p role="alert" data-testid="sketch-save-failed" className={`${styles.saveFailed} ${styles.sketchSaveFailed}`}>
      草圖尚未儲存
      <button type="button" className={styles.retry} onClick={() => saver.retry(pageId)}>
        重試
      </button>
    </p>
  );
}
