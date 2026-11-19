import { DEFAULT_PLACEMENT, type Placement } from "@/api/client";

/**
 * 角色在背景上的位置（與後端 app/services/compose.py 相同語意）：
 * cx、cy＝角色中心相對背景（0–1），height＝角色高度／背景高度（0.1–1.0），flip＝水平翻轉。
 * 角色指「依遮罩外框裁好的去背圖」（cutout），寬高比用它的實際尺寸。
 */
export const PLACEMENT_MIN_HEIGHT = 0.1;
export const PLACEMENT_MAX_HEIGHT = 1;

const round4 = (v: number) => Math.round(v * 10000) / 10000;
const clamp = (v: number, lo: number, hi: number, fallback: number) =>
  Number.isFinite(v) ? round4(Math.min(hi, Math.max(lo, v))) : fallback;

/** 夾到合法範圍，四捨五入到小數 4 位（後端比對既有合成圖用的精度）；非有限數字用預設值。 */
export function clampPlacement(p: Placement): Placement {
  return {
    cx: clamp(p.cx, 0, 1, DEFAULT_PLACEMENT.cx),
    cy: clamp(p.cy, 0, 1, DEFAULT_PLACEMENT.cy),
    height: clamp(p.height, PLACEMENT_MIN_HEIGHT, PLACEMENT_MAX_HEIGHT, DEFAULT_PLACEMENT.height),
    flip: p.flip,
  };
}

/** 移動（相對背景寬／高）。 */
export function movePlacement(p: Placement, dxRel: number, dyRel: number): Placement {
  return clampPlacement({ ...p, cx: p.cx + dxRel, cy: p.cy + dyRel });
}

/** 以中心為準改高度（相對背景高）。 */
export function resizePlacement(p: Placement, dHeightRel: number): Placement {
  return clampPlacement({ ...p, height: p.height + dHeightRel });
}

/**
 * 角色框（px，可超出舞台；超出的部分畫面與合成都會裁掉）。
 * poseAspect＝去背圖寬／高；與後端相同：高＝height×舞台高，寬＝高×poseAspect，以 (cx, cy) 為中心。
 */
export function placementBox(
  p: Placement,
  poseAspect: number,
  stageW: number,
  stageH: number,
): { left: number; top: number; width: number; height: number } {
  const height = p.height * stageH;
  const width = height * poseAspect;
  return { left: p.cx * stageW - width / 2, top: p.cy * stageH - height / 2, width, height };
}

/**
 * 拖右下角把手：左上角固定、等比縮放。
 * k＝角色框寬（相對舞台寬）／高（相對舞台高）＝ poseAspect × 舞台高 ／ 舞台寬。
 * 水平與垂直拉動各換算成一個新高度，取較大者（把手跟著拉得比較多的方向）。
 */
export function cornerResize(start: Placement, k: number, dxRel: number, dyRel: number): Placement {
  const w = start.height * k;
  const left = start.cx - w / 2;
  const top = start.cy - start.height / 2;
  const fromY = start.height + dyRel;
  const fromX = (w + dxRel) / k;
  const h = Math.min(PLACEMENT_MAX_HEIGHT, Math.max(PLACEMENT_MIN_HEIGHT, Math.max(fromX, fromY)));
  return clampPlacement({ ...start, height: h, cx: left + (h * k) / 2, cy: top + h / 2 });
}

/** 編輯頁快捷鈕：位置（左右）與大小（見 docs/superpowers/specs/2026-11-19-position-presets-design.md）。 */
export type PresetSide = "left" | "center" | "right";
export type PresetSize = "small" | "medium" | "large";
export const PRESET_CX: Record<PresetSide, number> = { left: 0.26, center: 0.5, right: 0.74 };
export const PRESET_HEIGHT: Record<PresetSize, number> = { small: 0.42, medium: 0.6, large: 0.78 };
const PRESET_TOLERANCE = 0.005;

/** 只改左右位置；大小、高度、翻轉不變。 */
export function withSide(p: Placement, side: PresetSide): Placement {
  return clampPlacement({ ...p, cx: PRESET_CX[side] });
}

/** 只改大小；左右不變，腳（底緣 cy + height/2）留在原處。 */
export function withSize(p: Placement, size: PresetSize): Placement {
  const height = PRESET_HEIGHT[size];
  return clampPlacement({ ...p, height, cy: p.cy + p.height / 2 - height / 2 });
}

const matching = <K extends string>(table: Record<K, number>, v: number): K | null =>
  (Object.keys(table) as K[]).find((k) => Math.abs(table[k] - v) < PRESET_TOLERANCE) ?? null;

/** 目前位置符合哪個左右選項；拖曳到別處時 null。 */
export function currentSide(p: Placement): PresetSide | null {
  return matching(PRESET_CX, p.cx);
}

/** 目前大小符合哪個選項；拖曳縮放到別的大小時 null。 */
export function currentSize(p: Placement): PresetSize | null {
  return matching(PRESET_HEIGHT, p.height);
}
