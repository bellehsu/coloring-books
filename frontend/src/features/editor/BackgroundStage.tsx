"use client";

import { useState, useSyncExternalStore } from "react";
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
 * 背景模式的舞台：有選用背景時預設顯示背景圖，上方可切換「背景圖／畫構圖」；沒有背景時直接是畫板與提示。
 * 畫板的存檔、whenIdle、失敗重試沿用 sketchSaver（與 Phase 4 相同）。
 */
export function BackgroundStage({ bookId, page, layout, background }: BackgroundStageProps) {
  const qc = useQueryClient();
  const sketchSaver = sketchSaverFor(qc);
  const pageId = page.id;
  const [gw, gh] = layout.generation_size;
  // 使用者明確選的檢視；沒選過時：有背景看背景圖，沒有就畫板。
  const [choice, setChoice] = useState<"image" | "sketch" | null>(null);
  const hasImage = !!background?.image_url;
  const view = hasImage ? (choice ?? "image") : "sketch";
  // 這個 session 存過（可能還在送、或失敗）→ 用最後存的內容；null＝空白。沒存過才用伺服器的 sketch_url。
  const pending = sketchSaver.latestUrl(pageId);
  const sketchSeed = pending !== undefined ? pending : page.sketch_url;
  return (
    <>
      {hasImage ? (
        <div className={styles.viewToggle} role="group" aria-label="舞台內容">
          <button type="button" className={styles.viewBtn} data-on={view === "image"} aria-pressed={view === "image"} onClick={() => setChoice("image")}>
            背景圖
          </button>
          <button type="button" className={styles.viewBtn} data-on={view === "sketch"} aria-pressed={view === "sketch"} onClick={() => setChoice("sketch")}>
            畫構圖
          </button>
        </div>
      ) : (
        <p className={styles.stageHint}>可以只寫場景描述，也可以先畫構圖</p>
      )}
      {view === "image" && background?.image_url ? (
        <div className={styles.bgPaper} style={{ aspectRatio: `${background.width} / ${background.height}` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={background.image_url} alt="背景圖" className={styles.bgImg} />
        </div>
      ) : (
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
      )}
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
