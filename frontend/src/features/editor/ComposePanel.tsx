"use client";

import { useRef, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DEFAULT_PLACEMENT, type Page, type Placement } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { placementSaverFor, type PlacementSaver } from "./placementSaver";
import styles from "./Inspector.module.css";
import editorStyles from "./EditorPage.module.css";
import own from "./ComposePanel.module.css";

export interface ComposePanelProps {
  /** 呼叫端用 key={page.id}。 */
  page: Page;
  /** 目前位置（同 ComposeStage）。 */
  placement: Placement;
  /** 改位置（水平翻轉、重設位置）；EditorPage 自動存。 */
  onPlacementChange: (p: Placement) => void;
  /**
   * 確認合成：EditorPage 先等舊選圖的修線寫完，useCompose 再等位置寫完（placementSaver.whenIdle），
   * 然後 POST /compose；成功後切到「線稿與修線」。失敗時 EditorPage 顯示 toast，promise 照常 resolve。
   */
  onConfirm: () => Promise<unknown>;
  /** 確認合成整段流程進行中（停用按鈕防連點；舞台同時停用）。 */
  confirming: boolean;
}

const pct = (v: number) => `${Math.round(v * 100)}%`;

/**
 * 合成模式的面板：目前位置、水平翻轉、重設位置（保留翻轉）、「確認合成」。
 * 位置寫入失敗時持續顯示「位置尚未儲存」＋重試（toast 會消失）。
 * page.compose_pending（目前選定圖不是這組背景＋角色動作＋位置的合成圖）時持續提示要確認合成。
 */
export function ComposePanel({ page, placement, onPlacementChange, onConfirm, confirming }: ComposePanelProps) {
  const saver = placementSaverFor(useQueryClient());
  // 父層的 confirming 要等下一次 render 才到；同一輪的第二下點擊用 ref 擋
  const startedRef = useRef(false);

  const confirm = async () => {
    if (confirming || startedRef.current) return;
    startedRef.current = true;
    try {
      await onConfirm();
    } finally {
      startedRef.current = false;
    }
  };

  return (
    <section aria-label="合成設定" className={styles.panel}>
      <h2 className={styles.h2}>合成</h2>
      <p className={styles.note2}>把角色拖到背景上想要的位置，拖右下角把手調整大小。確認後產生合成圖，接著到「線稿與修線」。</p>
      <p role="status" className={own.readout}>
        水平 {pct(placement.cx)}・垂直 {pct(placement.cy)}・高度 {pct(placement.height)}
      </p>
      <div className={own.row}>
        <Button
          aria-pressed={placement.flip}
          disabled={confirming}
          onClick={() => onPlacementChange({ ...placement, flip: !placement.flip })}
        >
          水平翻轉
        </Button>
        <Button
          disabled={confirming}
          onClick={() => onPlacementChange({ ...DEFAULT_PLACEMENT, flip: placement.flip })}
        >
          重設位置
        </Button>
      </div>
      {page.compose_pending && (
        <p role="status" data-testid="compose-pending" className={own.pending}>
          位置或素材已變更，按「確認合成」更新本頁圖片
        </p>
      )}
      <PlacementSaveStatus saver={saver} pageId={page.id} />
      <Button variant="primary" size="lg" disabled={confirming} onClick={() => void confirm()}>
        {confirming ? "合成中…" : "確認合成"}
      </Button>
    </section>
  );
}

/** 位置最後一次寫入失敗時持續顯示（toast 會消失）＋重試；與草圖的 SketchSaveStatus 相同。 */
function PlacementSaveStatus({ saver, pageId }: { saver: PlacementSaver; pageId: number }) {
  const failed = useSyncExternalStore(
    saver.subscribe,
    () => saver.failed(pageId),
    () => false,
  );
  if (!failed) return null;
  return (
    <p role="alert" data-testid="placement-save-failed" className={`${editorStyles.saveFailed} ${editorStyles.sketchSaveFailed}`}>
      位置尚未儲存
      <button type="button" className={editorStyles.retry} onClick={() => saver.retry(pageId)}>
        重試
      </button>
    </p>
  );
}
