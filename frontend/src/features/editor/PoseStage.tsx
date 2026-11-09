"use client";

import type { Generation } from "@/api/client";
import styles from "./EditorPage.module.css";

export interface PoseStageProps {
  /** 頁面選用的角色動作（含 cutout_url）；沒有或載入中 undefined。 */
  pose: Generation | undefined;
}

/** 角色模式的舞台：顯示後端裁好的去背圖（cutout_url）放在棋盤格底上，讓使用者看出去背是否乾淨；沒有時顯示空狀態。 */
export function PoseStage({ pose }: PoseStageProps) {
  if (!pose?.cutout_url) return <p className={styles.stageMsg}>{pose?.image_url ? "這個角色動作沒有去背圖" : "還沒有角色動作"}</p>;
  return (
    <div className={styles.poseBoard} style={{ aspectRatio: `${pose.width} / ${pose.height}` }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={pose.cutout_url} alt="角色動作（去背預覽）" className={styles.poseImg} />
    </div>
  );
}
