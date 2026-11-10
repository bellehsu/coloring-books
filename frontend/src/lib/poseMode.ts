/** 角色動作的產生方式：精準（Qwen-Image-Edit-2511）／快速（SDXL）。 */
export type PoseMode = "precise" | "fast";

export const POSE_MODE_KEY = "coloring.poseMode";
const DEFAULT: PoseMode = "precise";

const isPoseMode = (v: unknown): v is PoseMode => v === "precise" || v === "fast";

/** 這個瀏覽器記住的產生方式；沒存過、值不認得或 localStorage 不能用（無痕視窗等）→ 精準。 */
export function readPoseMode(): PoseMode {
  try {
    const v = localStorage.getItem(POSE_MODE_KEY);
    return isPoseMode(v) ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

/** 記住產生方式；寫不進去就算了（只是少了記憶）。 */
export function writePoseMode(m: PoseMode): void {
  try {
    localStorage.setItem(POSE_MODE_KEY, m);
  } catch {
    /* 無痕視窗、容量已滿：不影響產生 */
  }
}

/**
 * 實際要用的產生方式：可用狀態還沒載入 → 記住的；精準不可用而快速可用 → 快速；其他 → 記住的。
 * 退回快速只影響畫面與送出，不改寫記憶：精準恢復可用後自動回到使用者選的。
 */
export function effectiveMode(
  stored: PoseMode,
  avail: { precise: boolean; fast: boolean } | undefined,
): PoseMode {
  if (!avail) return stored;
  if (!avail.precise && avail.fast) return "fast";
  return stored;
}
