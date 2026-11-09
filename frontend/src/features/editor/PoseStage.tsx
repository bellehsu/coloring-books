"use client";

import type { Generation } from "@/api/client";
import styles from "./EditorPage.module.css";

export interface PoseStageProps {
  /** 頁面選用的角色動作（含 image_url／mask_url）；沒有或載入中 undefined。 */
  pose: Generation | undefined;
}

/** 角色模式的舞台：以遮罩去背後放在棋盤格底上，讓使用者看出去背是否乾淨；沒有時顯示空狀態。 */
export function PoseStage({ pose }: PoseStageProps) {
  if (!pose?.image_url) return <p className={styles.stageMsg}>還沒有角色動作</p>;
  const mask = pose.mask_url ? `url("${pose.mask_url}")` : undefined;
  const cut = mask
    ? ({
        maskImage: mask,
        WebkitMaskImage: mask,
        maskMode: "luminance",
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      } as React.CSSProperties)
    : undefined;
  return (
    <div className={styles.poseBoard} style={{ aspectRatio: `${pose.width} / ${pose.height}` }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={pose.image_url} alt="角色動作（去背預覽）" className={styles.poseImg} style={cut} />
    </div>
  );
}
