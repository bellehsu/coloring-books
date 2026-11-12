"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from "react";
import { IconButton } from "@/components/ui/IconButton";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import {
  ERASE_WIDTH,
  clampWidth,
  drawStrokes,
  simplify,
  toRelative,
  validate,
  type Stroke,
  type StrokeCtx,
  type StrokeOp,
} from "@/lib/strokes";
import { ZOOM_MAX, ZOOM_MIN, clampZoom, placePaper, stepZoom, wheelZoom } from "./zoom";
import styles from "./LineFixCanvas.module.css";

export interface LineFixCanvasProps {
  /** 伺服器渲染好的線稿（已套用修改圖層），帶 ?v=；只有字串改變才會重載圖片。 */
  lineartUrl: string;
  /** 線稿像素大小 [w, h]（book.layout.content_px，300 DPI），筆畫座標以此換算。 */
  contentPx: readonly [number, number];
  /** QA 缺口，相對座標 [x, y]。 */
  gaps: readonly (readonly [number, number])[];
  /** 目前完整的修改圖層（受控）。 */
  strokes: Stroke[];
  /**
   * 每一筆結束（或復原）時呼叫，參數是新的完整筆畫陣列。呼叫端要同步 setState 並 push 到 writeQueue。
   * 連續快畫時即使父層還沒重繪，下一筆也會接在上一次 onChange 的結果後面，不會掉筆。
   */
  onChange: (strokes: Stroke[]) => void;
  /** 頁面 line_width_px：補線寬（夾在 2–64）。擦除固定 24。 */
  lineWidthPx: number;
  /**
   * 縮放（1 = 100% = 紙張基準寬 min(舞台寬, 440 px)；範圍 0.5–4）。
   * 有給 onZoomChange 就是受控：工具列不顯示內建 −／＋，請把 <ZoomControls> 放在舞台標題列。
   * 沒給 onZoomChange：元件自己管縮放，−／＋ 顯示在浮動工具列。
   */
  zoom?: number;
  onZoomChange?: (zoom: number) => void;
  /** 寫入佇列忙碌中：狀態列顯示「更新中…」（橘色圈可能還是舊的）。 */
  pending?: boolean;
  /** 取代狀態列（缺口數／更新中）的內容，以 role="alert" 顯示：例如「修線尚未儲存」＋重試。 */
  statusOverride?: ReactNode;
  /** 一開始用的工具（預設補線）。 */
  initialTool?: StrokeOp;
  /** 不顯示工具列的「復原」（呼叫端有自己的復原）。 */
  hideUndo?: boolean;
}

/** 疊層 canvas 最長邊（內容 2551×3579 時約 1460×2048，記憶體約 12 MB）。 */
const OVERLAY_MAX_SIDE = 2048;
/** 紙張基準寬。 */
const BASE_PAPER_W = 440;

interface StrokeDrag {
  kind: "stroke";
  pointerId: number;
  op: StrokeOp;
  width: number;
  points: [number, number][];
}
interface PanDrag {
  kind: "pan";
  pointerId: number;
  sx: number;
  sy: number;
  x0: number;
  y0: number;
}

type OverlayCtx = StrokeCtx & Pick<CanvasRenderingContext2D, "setTransform" | "clearRect">;

const TOOLS: { id: StrokeOp; label: string; icon: keyof typeof Icons }[] = [
  { id: "draw", label: "補線", icon: "pen" },
  { id: "erase", label: "擦除", icon: "eraser" },
];

function isEditable(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
}

const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;

export function ZoomControls({ zoom, onZoomChange }: { zoom: number; onZoomChange: (z: number) => void }) {
  const z = clampZoom(zoom);
  return (
    <div role="group" aria-label="縮放" className={styles.zoom}>
      <IconButton
        label="縮小"
        size={40}
        className={styles.tool}
        disabled={z <= ZOOM_MIN}
        onClick={() => onZoomChange(stepZoom(z, -1))}
      >
        <span aria-hidden="true" className={styles.glyph}>−</span>
      </IconButton>
      <span className={styles.zoomValue}>{Math.round(z * 100)}%</span>
      <IconButton
        label="放大"
        size={40}
        className={styles.tool}
        disabled={z >= ZOOM_MAX}
        onClick={() => onZoomChange(stepZoom(z, 1))}
      >
        <span aria-hidden="true" className={styles.glyph}>＋</span>
      </IconButton>
    </div>
  );
}

export function LineFixCanvas({
  lineartUrl,
  contentPx,
  gaps,
  strokes,
  onChange,
  lineWidthPx,
  zoom: zoomProp,
  onZoomChange,
  pending = false,
  statusOverride,
  initialTool = "draw",
  hideUndo = false,
}: LineFixCanvasProps) {
  const toast = useToast();
  const [w, h] = contentPx;
  const viewportRef = useRef<HTMLDivElement>(null);
  const paperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<StrokeDrag | PanDrag | null>(null);
  /** 最新的完整筆畫：props 改變時同步；onChange 之後立刻更新（父層重繪前的下一筆不會掉）。 */
  const latestRef = useRef<Stroke[]>(strokes);
  const spaceRef = useRef(false);
  const hoverRef = useRef(false);

  const [tool, setTool] = useState<StrokeOp>(initialTool);
  const [internalZoom, setInternalZoom] = useState(1);
  const [center, setCenter] = useState({ cx: 0.5, cy: 0.5 });
  const [vpWidth, setVpWidth] = useState(0);
  const [mode, setMode] = useState<"draw" | "pan-ready" | "panning">("draw");

  const controlled = onZoomChange !== undefined;
  const zoom = clampZoom(controlled ? (zoomProp ?? 1) : internalZoom);
  const setZoom = useCallback(
    (z: number) => {
      const c = clampZoom(z);
      if (onZoomChange) onZoomChange(c);
      else setInternalZoom(c);
    },
    [onZoomChange],
  );

  // ---------- 版面 ----------
  const vpW = vpWidth > 0 ? vpWidth : BASE_PAPER_W;
  const baseW = Math.min(vpW, BASE_PAPER_W);
  const baseH = (baseW * h) / w;
  const dispW = baseW * zoom;
  const dispH = baseH * zoom;
  const vpH = baseH;
  const place = placePaper({ vpW, vpH, dispW, dispH, cx: center.cx, cy: center.cy });

  const viewRef = useRef({ place, dispW, dispH, vpW, vpH, baseW, zoom, setZoom });
  useLayoutEffect(() => {
    viewRef.current = { place, dispW, dispH, vpW, vpH, baseW, zoom, setZoom };
  });

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    if (typeof ResizeObserver === "undefined") {
      setVpWidth(el.clientWidth);
      return;
    }
    const ro = new ResizeObserver((entries) => setVpWidth(entries[0]?.contentRect.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ---------- 疊層 ----------
  const k = Math.min(1, OVERLAY_MAX_SIDE / Math.max(w, h));
  const cw = Math.max(1, Math.round(w * k));
  const ch = Math.max(1, Math.round(h * k));

  const ctxOf = useCallback((): OverlayCtx | null => {
    const ctx = (canvasRef.current?.getContext("2d") as OverlayCtx | null) ?? null;
    ctx?.setTransform(cw / w, 0, 0, ch / h, 0, 0);
    return ctx;
  }, [cw, ch, w, h]);

  /**
   * 重畫整個疊層：全部筆畫（＋拖曳中的那筆）。筆畫依序覆蓋，所以不論底圖已套用到哪一版，
   * 再疊一次全部筆畫的結果都等於「原線稿＋全部筆畫」；伺服器還沒重算時畫面就已經正確。
   * 例外：復原移除的筆畫若已在底圖裡，要等伺服器回新圖才會消失。
   */
  const redraw = useCallback(
    (list: readonly Stroke[]) => {
      const ctx = ctxOf();
      if (!ctx) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cw, ch);
      ctx.setTransform(cw / w, 0, 0, ch / h, 0, 0);
      drawStrokes(ctx, list, w, h);
      const d = dragRef.current;
      if (d?.kind === "stroke") drawStrokes(ctx, [{ op: d.op, width_px: d.width, points: d.points }], w, h);
    },
    [ctxOf, cw, ch, w, h],
  );

  useEffect(() => {
    latestRef.current = strokes;
    redraw(strokes);
  }, [strokes, redraw]);

  // ---------- 空白鍵平移 ----------
  useEffect(() => {
    const isSpace = (e: KeyboardEvent) => e.code === "Space" || e.key === " ";
    const down = (e: KeyboardEvent) => {
      if (!isSpace(e) || isEditable(e.target)) return;
      // 游標在畫布上：不要捲動頁面，也不要觸發焦點所在的按鈕（例如「復原」）
      if (hoverRef.current || dragRef.current) e.preventDefault();
      if (!spaceRef.current) {
        spaceRef.current = true;
        if (!dragRef.current) setMode("pan-ready");
      }
    };
    const up = (e: KeyboardEvent) => {
      if (!isSpace(e)) return;
      if (hoverRef.current && spaceRef.current) e.preventDefault();
      spaceRef.current = false;
      if (dragRef.current?.kind !== "pan") setMode("draw");
    };
    const blur = () => {
      spaceRef.current = false;
      if (dragRef.current?.kind !== "pan") setMode("draw");
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // ---------- 滾輪：Ctrl/⌘＋滾輪（含觸控板捏合）縮放；一般滾輪在紙張超出視窗時平移 ----------
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const v = viewRef.current;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const z2 = wheelZoom(v.zoom, e.deltaY);
        if (z2 === v.zoom) return;
        // 以游標為錨點：游標下的那一點縮放後仍在游標下
        const r = el.getBoundingClientRect();
        const ax = e.clientX - r.left;
        const ay = e.clientY - r.top;
        const ux = v.dispW > 0 ? (ax - v.place.x) / v.dispW : 0.5;
        const uy = v.dispH > 0 ? (ay - v.place.y) / v.dispH : 0.5;
        const dw = v.dispW * (z2 / v.zoom);
        const dh = v.dispH * (z2 / v.zoom);
        setCenter({ cx: (v.vpW / 2 - (ax - ux * dw)) / dw, cy: (v.vpH / 2 - (ay - uy * dh)) / dh });
        v.setZoom(z2);
        return;
      }
      if (v.dispW <= v.vpW && v.dispH <= v.vpH) return; // 沒有可平移的範圍：讓頁面正常捲動
      e.preventDefault();
      const x = v.place.x - e.deltaX;
      const y = v.place.y - e.deltaY;
      setCenter({ cx: (v.vpW / 2 - x) / v.dispW, cy: (v.vpH / 2 - y) / v.dispH });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // ---------- 指標 ----------
  const relPoint = (e: PointerEvent): [number, number] => {
    const r = paperRef.current!.getBoundingClientRect();
    // 螢幕 → 內容像素（像素 i 的中心在 i + 0.5，所以減 0.5 得到像素索引座標）→ 相對座標
    const px = r.width > 0 ? ((e.clientX - r.left) / r.width) * w - 0.5 : 0;
    const py = r.height > 0 ? ((e.clientY - r.top) / r.height) * h - 0.5 : 0;
    return toRelative(px, py, w, h);
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (dragRef.current) return; // 一次只處理一個指標
    const pan = e.button === 1 || (e.button === 0 && spaceRef.current);
    if (pan) {
      e.preventDefault(); // 中鍵：避免瀏覽器自動捲動
      viewportRef.current?.setPointerCapture?.(e.pointerId);
      const { place: p } = viewRef.current;
      dragRef.current = { kind: "pan", pointerId: e.pointerId, sx: e.clientX, sy: e.clientY, x0: p.x, y0: p.y };
      setMode("panning");
      return;
    }
    if (e.button !== 0 || !paperRef.current?.contains(e.target as Node)) return;
    e.preventDefault();
    viewportRef.current?.setPointerCapture?.(e.pointerId);
    const p = relPoint(e);
    const width = tool === "draw" ? clampWidth(lineWidthPx) : ERASE_WIDTH;
    dragRef.current = { kind: "stroke", pointerId: e.pointerId, op: tool, width, points: [p] };
    const ctx = ctxOf();
    if (ctx) drawStrokes(ctx, [{ op: tool, width_px: width, points: [p] }], w, h);
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d || e.pointerId !== d.pointerId) return;
    if (d.kind === "pan") {
      const v = viewRef.current;
      const x = d.x0 + (e.clientX - d.sx);
      const y = d.y0 + (e.clientY - d.sy);
      setCenter({ cx: (v.vpW / 2 - x) / v.dispW, cy: (v.vpH / 2 - y) / v.dispH });
      return;
    }
    const p = relPoint(e);
    const last = d.points[d.points.length - 1];
    d.points.push(p);
    // 立即回饋：只畫新的一段（圓頭讓折線連續）
    const ctx = ctxOf();
    if (ctx) drawStrokes(ctx, [{ op: d.op, width_px: d.width, points: [last, p] }], w, h);
  };

  /**
   * pointerup／pointercancel／lostpointercapture 都走這裡：結束拖曳（筆畫照常提交）。
   * 捕捉被瀏覽器收走（例如切換視窗）時若不清 dragRef，之後的 pointerdown 都會被忽略。
   * 正常放開時 releasePointerCapture 也會觸發 lostpointercapture，那時 dragRef 已清空，不會重複提交。
   */
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d || e.pointerId !== d.pointerId) return;
    dragRef.current = null;
    viewportRef.current?.releasePointerCapture?.(e.pointerId);
    if (d.kind === "pan") {
      setMode(spaceRef.current ? "pan-ready" : "draw");
      return;
    }
    const points = simplify(d.points);
    if (points.length === 0) return;
    const next = [...latestRef.current, { op: d.op, width_px: d.width, points }];
    const err = validate(next);
    if (err) {
      toast.show(err, "error");
      redraw(latestRef.current); // 擦掉預覽
      return;
    }
    latestRef.current = next;
    onChange(next);
  };

  const undo = () => {
    if (dragRef.current?.kind === "stroke" || latestRef.current.length === 0) return;
    const next = latestRef.current.slice(0, -1);
    latestRef.current = next;
    onChange(next);
  };

  const gapCount = gaps.length;
  const statusText = pending
    ? "更新中…"
    : gapCount === 0
      ? "沒有缺口"
      : `${gapCount} 個缺口會讓塗色漏出去（橘色圈）`;
  const statusTone = pending ? styles.statusPending : gapCount === 0 ? styles.statusOk : styles.statusGap;

  return (
    <div className={styles.root}>
      <div role="toolbar" aria-label="修線工具" className={styles.toolbar}>
        {TOOLS.map((t) => {
          const Icon = Icons[t.icon];
          return (
            <button
              key={t.id}
              type="button"
              className={styles.textTool}
              data-active={tool === t.id ? "true" : undefined}
              aria-pressed={tool === t.id}
              onClick={() => setTool(t.id)}
            >
              <Icon size={18} />
              {t.label}
            </button>
          );
        })}
        {!hideUndo && (
          <>
            <span aria-hidden="true" className={styles.divider} />
            <IconButton
              label="復原"
              size={40}
              className={styles.tool}
              disabled={strokes.length === 0}
              onClick={undo}
            >
              <Icons.undo />
            </IconButton>
          </>
        )}
        {!controlled && (
          <>
            <span aria-hidden="true" className={styles.divider} />
            <ZoomControls zoom={zoom} onZoomChange={setZoom} />
          </>
        )}
      </div>

      <div
        ref={viewportRef}
        data-part="viewport"
        data-mode={mode}
        className={styles.viewport}
        style={{ height: vpH }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onLostPointerCapture={onPointerUp}
        onPointerEnter={() => (hoverRef.current = true)}
        onPointerLeave={() => (hoverRef.current = false)}
      >
        <div
          ref={paperRef}
          data-part="paper"
          className={styles.paper}
          style={{ width: dispW, height: dispH, transform: `translate(${place.x}px, ${place.y}px)` }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- 後端動態 PNG（帶 ?v=），不走 next/image 最佳化 */}
          <img
            src={lineartUrl}
            alt="線稿，橘色圈標出缺口"
            draggable={false}
            className={styles.image}
            onError={() => toast.show("線稿載入失敗，請重新整理", "error")}
          />
          <canvas ref={canvasRef} width={cw} height={ch} className={styles.overlay} aria-hidden="true" />
          {gaps.map(([x, y], i) => (
            <span
              key={`${i}-${x}-${y}`}
              aria-hidden="true"
              data-part="gap"
              className={styles.gap}
              style={{ left: pct(x), top: pct(y) }}
            />
          ))}
        </div>
      </div>

      {statusOverride ? (
        <div role="alert" className={`${styles.status} ${styles.statusGap}`}>
          {statusOverride}
        </div>
      ) : (
        <p role="status" className={`${styles.status} ${statusTone}`}>
          {statusText}
        </p>
      )}
    </div>
  );
}
