/**
 * 修線圖層的向量筆畫（純函式）。座標規則與後端 lineart_render.apply_strokes 相同：
 *   x = px / (w - 1)、y = py / (h - 1)，px/py 是線稿（content_px，300 DPI）的像素索引；
 *   後端用 round(x * (w - 1)) 還原像素。
 * 限制對齊 backend/app/api/schemas.py 的 StrokeIn / EditsIn。
 */

export type StrokeOp = "draw" | "erase";
export type Stroke = { op: StrokeOp; width_px: number; points: [number, number][] };

export const STROKE_WIDTH_MIN = 2;
export const STROKE_WIDTH_MAX = 64;
export const MAX_STROKE_POINTS = 2000;
export const MAX_STROKES = 500;
export const MAX_TOTAL_POINTS = 20000;
/** 擦除寬（300 DPI px）。補線寬＝頁面 line_width_px。 */
export const ERASE_WIDTH = 24;

const clamp01 = (v: number) => (Number.isNaN(v) ? 0 : Math.min(1, Math.max(0, v)));

/** 像素索引 → 相對座標，夾在 0–1。寬或高只有 1 px 時分母當 1。 */
export function toRelative(px: number, py: number, w: number, h: number): [number, number] {
  return [clamp01(px / Math.max(1, w - 1)), clamp01(py / Math.max(1, h - 1))];
}

/** 相對座標 → 像素索引（toRelative 的反函數，未取整）。 */
export function toPixel(x: number, y: number, w: number, h: number): [number, number] {
  return [x * Math.max(1, w - 1), y * Math.max(1, h - 1)];
}

/** 線寬夾在後端允許的 2–64；非有限值用擦除寬。 */
export function clampWidth(px: number): number {
  if (!Number.isFinite(px)) return ERASE_WIDTH;
  return Math.min(STROKE_WIDTH_MAX, Math.max(STROKE_WIDTH_MIN, px));
}

/**
 * 去掉離上一個保留點小於 minDistRel（相對座標距離）的點；起點一定保留；終點與上一個保留點不同就保留（全部重合則只剩 1 點）。
 * 結果超過 MAX_STROKE_POINTS 時均勻抽樣（保留頭尾）。
 */
export function simplify(points: [number, number][], minDistRel = 0.0015): [number, number][] {
  if (points.length === 0) return [];
  const min2 = minDistRel * minDistRel;
  const out: [number, number][] = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const p = points[i];
    const last = out[out.length - 1];
    const dx = p[0] - last[0];
    const dy = p[1] - last[1];
    const d2 = dx * dx + dy * dy;
    // 終點只要與上一個保留點不同就保留：筆畫一定結束在放開的位置
    if (d2 >= min2 || (i === points.length - 1 && d2 > 0)) {
      out.push(p);
    }
  }
  if (out.length <= MAX_STROKE_POINTS) return out;
  const n = MAX_STROKE_POINTS;
  const step = (out.length - 1) / (n - 1);
  return Array.from({ length: n }, (_, i) => out[Math.round(i * step)]);
}

/** 對齊後端 EditsIn：合法回 null，否則回中文錯誤訊息。 */
export function validate(strokes: Stroke[]): string | null {
  if (strokes.length > MAX_STROKES) return `修線最多 ${MAX_STROKES} 筆，請先復原或用擦除整理`;
  let total = 0;
  for (const s of strokes) {
    if (s.op !== "draw" && s.op !== "erase") return "筆畫類型不正確";
    if (!Number.isFinite(s.width_px) || s.width_px < STROKE_WIDTH_MIN || s.width_px > STROKE_WIDTH_MAX)
      return `線寬必須在 ${STROKE_WIDTH_MIN}–${STROKE_WIDTH_MAX} px`;
    if (s.points.length < 1 || s.points.length > MAX_STROKE_POINTS)
      return `每一筆需要 1–${MAX_STROKE_POINTS} 個點`;
    for (const [x, y] of s.points) {
      if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 1 || y < 0 || y > 1)
        return "筆畫座標超出線稿範圍";
    }
    total += s.points.length;
  }
  if (total > MAX_TOTAL_POINTS) return `修線全部最多 ${MAX_TOTAL_POINTS} 點，請先復原幾筆`;
  return null;
}

export type StrokeCtx = Pick<
  CanvasRenderingContext2D,
  | "save"
  | "restore"
  | "beginPath"
  | "moveTo"
  | "lineTo"
  | "arc"
  | "stroke"
  | "fill"
  | "strokeStyle"
  | "fillStyle"
  | "lineWidth"
  | "lineCap"
  | "lineJoin"
>;

const INK = "#000000";
const PAPER = "#ffffff";

/**
 * 依序把筆畫畫到 ctx（座標單位＝內容像素；呼叫端自行 setTransform 縮放）。
 * 像素索引 i 的中心在 canvas 座標 i + 0.5，所以點畫在 x*(w-1)+0.5。補線黑、擦除白；圓頭圓角，
 * 近似後端 polylines + 每點畫圓。單點筆畫畫成直徑＝線寬的實心圓。
 */
export function drawStrokes(ctx: StrokeCtx, strokes: readonly Stroke[], w: number, h: number) {
  for (const s of strokes) {
    if (s.points.length === 0) continue;
    const color = s.op === "draw" ? INK : PAPER;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = s.width_px;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    const [x0, y0] = toPixel(s.points[0][0], s.points[0][1], w, h);
    if (s.points.length === 1) {
      ctx.arc(x0 + 0.5, y0 + 0.5, s.width_px / 2, 0, Math.PI * 2);
      ctx.fill();
    } else {
      ctx.moveTo(x0 + 0.5, y0 + 0.5);
      for (let i = 1; i < s.points.length; i++) {
        const [x, y] = toPixel(s.points[i][0], s.points[i][1], w, h);
        ctx.lineTo(x + 0.5, y + 0.5);
      }
      ctx.stroke();
    }
    ctx.restore();
  }
}
