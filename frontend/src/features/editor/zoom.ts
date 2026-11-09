/** 修線畫布的縮放／平移（純函式）。zoom 1 = 紙張基準寬（min(舞台寬, 440 px)，同草圖）。 */

export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 4;
/** −／＋ 按鈕的刻度。 */
export const ZOOM_STEPS = [0.5, 0.75, 1, 1.5, 2, 3, 4] as const;

export function clampZoom(z: number): number {
  if (!Number.isFinite(z)) return 1;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
}

const EPS = 1e-6;

/** 往上（dir=1）或往下（dir=-1）走到下一個刻度；已在端點則不動。 */
export function stepZoom(z: number, dir: 1 | -1): number {
  const cur = clampZoom(z);
  if (dir > 0) return ZOOM_STEPS.find((s) => s > cur + EPS) ?? ZOOM_MAX;
  return [...ZOOM_STEPS].reverse().find((s) => s < cur - EPS) ?? ZOOM_MIN;
}

/** 滾輪連續縮放：deltaY < 0（往上捲／觸控板張開）放大。 */
export function wheelZoom(z: number, deltaY: number): number {
  return clampZoom(z * Math.exp(-deltaY * 0.002));
}

export interface PlaceInput {
  /** 視窗（可見區）大小，螢幕 px。 */
  vpW: number;
  vpH: number;
  /** 紙張顯示大小，螢幕 px。 */
  dispW: number;
  dispH: number;
  /** 視窗中心對到紙張的哪一點（相對 0–1）。 */
  cx: number;
  cy: number;
}

/**
 * 算紙張左上角在視窗中的位置。某一軸紙比視窗小 → 該軸置中；比視窗大 → 依 center 放置並夾住，
 * 不露出紙外的舞台。也回傳夾過後實際的 center（元件目前不存回 state；平移與滾輪都從夾過的 x/y 反推 center，不會累積超出範圍）。
 */
export function placePaper({ vpW, vpH, dispW, dispH, cx, cy }: PlaceInput) {
  const axis = (vp: number, disp: number, c: number) => {
    if (disp <= vp || disp <= 0) return { pos: (vp - disp) / 2, c: 0.5 };
    const pos = Math.min(0, Math.max(vp - disp, vp / 2 - c * disp));
    return { pos, c: (vp / 2 - pos) / disp };
  };
  const ax = axis(vpW, dispW, cx);
  const ay = axis(vpH, dispH, cy);
  return { x: ax.pos, y: ay.pos, cx: ax.c, cy: ay.c };
}
