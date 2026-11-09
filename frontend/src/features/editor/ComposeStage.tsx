"use client";

import type { Placement } from "@/api/client";
import styles from "./EditorPage.module.css";

export interface ComposeStageProps {
  /** 選用背景的圖。 */
  backgroundUrl: string;
  /** 選用角色動作的圖與去背遮罩。 */
  poseUrl: string;
  maskUrl: string;
  /** 角色動作圖的寬／高。 */
  poseAspect: number;
  /** 目前位置（本 session 最後存的 → 伺服器草稿 → DEFAULT_PLACEMENT）。 */
  placement: Placement;
  /** 結束拖曳或按鍵時呼叫一次；EditorPage 會自動存（placementSaver，依序、最新為準）。 */
  onChange: (p: Placement) => void;
}

/**
 * 合成模式的舞台。
 * Task 8 佔位。Task 11 換成完整版：背景上疊去背角色；拖曳移動、右下把手等比縮放、鍵盤移動／縮放。
 */
export function ComposeStage({ backgroundUrl }: ComposeStageProps) {
  return (
    <p className={styles.stageMsg} data-bg={backgroundUrl}>
      合成預覽
    </p>
  );
}
