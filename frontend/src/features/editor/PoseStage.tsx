"use client";

import type { Generation } from "@/api/client";
import styles from "./EditorPage.module.css";

export interface PoseStageProps {
  /** 頁面選用的角色動作（page.pose_generation_id 的 generation，含 image_url／mask_url）；沒有或載入中 undefined。 */
  pose: Generation | undefined;
}

/**
 * 角色模式的舞台。
 * Task 8 佔位。Task 10 換成完整版：以遮罩去背後放在棋盤格底上（CSS mask-image 用 mask_url）；沒有時顯示空狀態。
 */
export function PoseStage({ pose }: PoseStageProps) {
  return <p className={styles.stageMsg}>{pose ? "已選用角色動作" : "還沒有角色動作"}</p>;
}
