/**
 * 構圖畫板的點陣繪圖邏輯（純函式）。
 * 只依賴下面 RasterCtx 這個 2D context 子集，測試可以傳假 context。
 * 畫布永遠是白底黑線：ControlNet scribble 輸入要的就是這個。
 */
import type { components } from "@/api/schema";

export type ShapeKind = "line" | "rect" | "circle";
export type StrokeTool = "brush" | "eraser";
export type Tool = StrokeTool | ShapeKind;

export interface Pt {
  x: number;
  y: number;
}

export type RasterCtx = Pick<
  CanvasRenderingContext2D,
  | "save"
  | "restore"
  | "beginPath"
  | "moveTo"
  | "lineTo"
  | "arc"
  | "ellipse"
  | "stroke"
  | "fill"
  | "strokeRect"
  | "fillRect"
  | "strokeStyle"
  | "fillStyle"
  | "lineWidth"
  | "lineCap"
  | "lineJoin"
>;

export const INK = "#000000";
export const PAPER = "#ffffff";
/** 筆寬固定 6 px（生成解析度）。 */
export const BRUSH_WIDTH = 6;
/** 橡皮擦寬（生成解析度）；6 px 在 440 px 顯示寬下只有約 3 px，太難擦。 */
export const ERASER_WIDTH = 24;
export const MAX_UNDO = 30;

function pen(ctx: RasterCtx, color: string, width: number) {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
}

/** 整張塗白（所有內容底下都要有白底）。 */
export function fillPaper(ctx: RasterCtx, width: number, height: number) {
  ctx.save();
  ctx.fillStyle = PAPER;
  ctx.fillRect(0, 0, width, height);
  ctx.restore();
}

/** brush／eraser：圓頭折線；eraser 畫白色。單點畫成實心圓點。 */
export function drawStroke(ctx: RasterCtx, tool: StrokeTool, points: Pt[], width: number) {
  if (points.length === 0) return;
  ctx.save();
  pen(ctx, tool === "eraser" ? PAPER : INK, width);
  ctx.beginPath();
  if (points.length === 1) {
    ctx.arc(points[0].x, points[0].y, width / 2, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i++) ctx.lineTo(points[i].x, points[i].y);
    ctx.stroke();
  }
  ctx.restore();
}

/** line：a→b；rect：a、b 為對角；circle：內切於 a、b 拖曳框的橢圓。 */
export function drawShape(ctx: RasterCtx, kind: ShapeKind, a: Pt, b: Pt, width: number) {
  ctx.save();
  pen(ctx, INK, width);
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  const w = Math.abs(b.x - a.x);
  const h = Math.abs(b.y - a.y);
  if (kind === "rect") {
    ctx.strokeRect(x, y, w, h);
  } else {
    ctx.beginPath();
    if (kind === "line") {
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
    } else {
      ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
    }
    ctx.stroke();
  }
  ctx.restore();
}

/** 所有像素的 R、G、B 都 ≥ threshold 才算空白（alpha 不看：畫布一律先塗白）。 */
export function isBlank(img: Pick<ImageData, "data">, threshold = 250): boolean {
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    if (d[i] < threshold || d[i + 1] < threshold || d[i + 2] < threshold) return false;
  }
  return true;
}

/** 螢幕座標 → 畫布像素（畫布以 CSS 等比縮放顯示）。 */
export function toCanvasPoint(
  event: { clientX: number; clientY: number },
  rect: { left: number; top: number; width: number; height: number },
  canvasSize: { width: number; height: number },
): Pt {
  return {
    x: ((event.clientX - rect.left) * canvasSize.width) / (rect.width || 1),
    y: ((event.clientY - rect.top) * canvasSize.height) / (rect.height || 1),
  };
}

/** 加一筆快照，最多保留 max 筆（丟最舊）；回傳新陣列。 */
export function pushHistory<T>(stack: readonly T[], item: T, max = MAX_UNDO): T[] {
  const next = [...stack, item];
  return next.length > max ? next.slice(next.length - max) : next;
}

/** 比例矩形（相對於內容區）。 */
export interface GuideBox {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface SketchGuides {
  trim: GuideBox;
  safe: GuideBox;
}

type LayoutBoxes = Pick<components["schemas"]["LayoutOut"], "content_px" | "trim_box_px" | "safe_box_px">;

/**
 * Book.layout → 參考線比例。trim_box_px／safe_box_px 是內容座標系 [x0, y0, x1, y1]，
 * 除以 content_px 得到比例；家用模式沒有參考線 → null。
 */
export function guidesFromLayout(layout: LayoutBoxes): SketchGuides | null {
  const { content_px: [cw, ch], trim_box_px: trim, safe_box_px: safe } = layout;
  if (!trim || !safe) return null;
  const box = ([x0, y0, x1, y1]: readonly number[]): GuideBox => ({
    x: x0 / cw,
    y: y0 / ch,
    w: (x1 - x0) / cw,
    h: (y1 - y0) / ch,
  });
  return { trim: box(trim), safe: box(safe) };
}
