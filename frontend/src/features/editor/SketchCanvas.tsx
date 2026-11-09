"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent, type PointerEvent } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import {
  BRUSH_WIDTH,
  ERASER_WIDTH,
  drawShape,
  drawStroke,
  fillPaper,
  isBlank,
  pushHistory,
  toCanvasPoint,
  type Pt,
  type RasterCtx,
  type SketchGuides,
  type Tool,
} from "@/lib/raster";
import styles from "./SketchCanvas.module.css";

export { guidesFromLayout, type SketchGuides } from "@/lib/raster";

export interface SketchCanvasProps {
  /** 繪本 layout.generation_size：畫布內部解析度＝輸出 PNG 尺寸。 */
  width: number;
  height: number;
  /** 裁切線／安全區（內容區比例，見 guidesFromLayout）；家用模式為 null。 */
  guides: SketchGuides | null;
  /**
   * 既有草圖（page.sketch_url，可帶 ?v=）。只在掛載時讀一次：存檔後 sketch_url 的 ?v= 會變，
   * 若重新載入會蓋掉正在畫的筆畫與復原紀錄。呼叫端換頁時請用 key={page.id} 重新掛載。
   */
  initialUrl: string | null | undefined;
  /**
   * 存檔：PNG（白底黑線）；畫布全白時給 null（呼叫端應刪除草圖，絕不上傳空白 PNG）。
   * 落筆結束 1 秒後呼叫（debounce）；卸載時若有未存變更會「同步」呼叫一次（見下方 flush）。
   * 可能在元件卸載後才被呼叫，呼叫端不要依賴元件仍在畫面上；寫入請依呼叫順序送出。
   */
  onSave: (blob: Blob | null) => void;
  /** 呼叫端 POST /books/{id}/reference-sketch，回傳擷取好的白底黑線 PNG。 */
  onUploadReference: (file: File) => Promise<Blob>;
}

type Ctx = RasterCtx & Pick<CanvasRenderingContext2D, "getImageData" | "putImageData" | "drawImage">;

interface Drag {
  pointerId: number;
  tool: Tool;
  start: Pt;
  last: Pt;
  moved: boolean;
  /** 落筆前的畫布：復原用；形狀工具拖曳中每次先還原它再畫預覽。 */
  snapshot: ImageData;
}

const SAVE_DEBOUNCE_MS = 1000;

const TOOLS: { id: Tool; label: string; icon: keyof typeof Icons }[] = [
  { id: "brush", label: "筆刷", icon: "brush" },
  { id: "line", label: "直線", icon: "line" },
  { id: "rect", label: "方框", icon: "rect" },
  { id: "circle", label: "圓", icon: "circle" },
  { id: "eraser", label: "橡皮擦", icon: "eraser" },
];

function ctxOf(canvas: HTMLCanvasElement | null): Ctx | null {
  return (canvas?.getContext("2d", { willReadFrequently: true }) as Ctx | null) ?? null;
}

function readAll(canvas: HTMLCanvasElement, ctx: Ctx): ImageData {
  return ctx.getImageData(0, 0, canvas.width, canvas.height);
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("圖片載入失敗"));
    img.src = src;
  });
}

/** 同步把 canvas 轉成 PNG Blob（卸載 flush 用；toBlob 是非同步的）。 */
function pngBlobSync(canvas: HTMLCanvasElement): Blob {
  const dataUrl = canvas.toDataURL("image/png");
  const bin = atob(dataUrl.slice(dataUrl.indexOf(",") + 1));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/png" });
}

export function SketchCanvas({ width, height, guides, initialUrl, onSave, onUploadReference }: SketchCanvasProps) {
  const toast = useToast();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const initialUrlRef = useRef(initialUrl);
  const onSaveRef = useRef(onSave);
  const historyRef = useRef<ImageData[]>([]);
  const dragRef = useRef<Drag | null>(null);
  const readyRef = useRef(false);
  const mountedRef = useRef(false);
  const dirtyRef = useRef(false);
  const saveSeqRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** saveNow 與 scheduleSave 互相呼叫（toBlob 失敗重試），用 ref 打斷循環依賴。 */
  const scheduleRef = useRef<(canvas: HTMLCanvasElement, isRetry?: boolean) => void>(() => {});

  const [tool, setTool] = useState<Tool>("brush");
  const [undoCount, setUndoCount] = useState(0);
  const [blank, setBlank] = useState(!initialUrl);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    onSaveRef.current = onSave;
  }, [onSave]);

  /**
   * 立即存檔。sync=false：canvas.toBlob（非同步）；sync=true：toDataURL 轉 Blob（同步，卸載時用）。
   * 空白一律給 null，不產生 PNG。每次存檔都帶流水號；較舊的 toBlob 若晚於較新的存檔才完成，直接丟掉，
   * 確保 onSave 的最後一次呼叫永遠對應畫布最新狀態。
   * toBlob 回傳 null（編碼失敗）時，經同一個 debounce 重試一次；再失敗就保持 dirty，等下一次變更或卸載 flush。
   */
  const saveNow = useCallback((canvas: HTMLCanvasElement, sync: boolean, isRetry = false) => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!dirtyRef.current) return;
    const ctx = ctxOf(canvas);
    if (!ctx) return;
    dirtyRef.current = false;
    const seq = ++saveSeqRef.current;
    if (isBlank(readAll(canvas, ctx))) {
      onSaveRef.current(null);
      return;
    }
    if (sync) {
      onSaveRef.current(pngBlobSync(canvas));
      return;
    }
    canvas.toBlob((blob) => {
      if (seq !== saveSeqRef.current) return;
      if (blob) {
        onSaveRef.current(blob);
        return;
      }
      dirtyRef.current = true;
      if (!isRetry && mountedRef.current) scheduleRef.current(canvas, true);
    }, "image/png");
  }, []);

  /** 1 秒後存檔（重設計時器）。 */
  const scheduleSave = useCallback(
    (canvas: HTMLCanvasElement, isRetry = false) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        if (mountedRef.current) saveNow(canvas, false, isRetry);
      }, SAVE_DEBOUNCE_MS);
    },
    [saveNow],
  );
  useEffect(() => {
    scheduleRef.current = scheduleSave;
  }, [scheduleSave]);

  /** 每次變更結束：更新空白狀態（控制留白提示），1 秒後存檔。 */
  const afterChange = useCallback(
    (canvas: HTMLCanvasElement, ctx: Ctx) => {
      setBlank(isBlank(readAll(canvas, ctx)));
      dirtyRef.current = true;
      scheduleSave(canvas);
    },
    [scheduleSave],
  );

  const pushUndo = (snapshot: ImageData) => {
    historyRef.current = pushHistory(historyRef.current, snapshot);
    setUndoCount(historyRef.current.length);
  };

  // 初始化：白底 → 有既有草圖就畫上去（縮放到畫布大小）。載入完成前不接受落筆，避免底圖蓋掉筆畫。
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = ctxOf(canvas);
    if (!canvas || !ctx) return;
    let cancelled = false;
    readyRef.current = false;
    historyRef.current = [];
    setUndoCount(0);
    fillPaper(ctx, width, height);
    const url = initialUrlRef.current;
    if (!url) {
      readyRef.current = true;
      return;
    }
    loadImage(url)
      .then((img) => {
        if (cancelled) return;
        ctx.drawImage(img, 0, 0, width, height);
        setBlank(false);
      })
      .catch(() => {
        if (cancelled) return;
        setBlank(true);
        toast.show("無法載入既有構圖，可以重新畫", "error");
      })
      .finally(() => {
        if (!cancelled) readyRef.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, [width, height, toast]);

  // 卸載 flush：還有未存的變更（debounce 還沒到）就同步呼叫 onSave，離開頁面不會掉最後一筆。
  // canvas 在 effect 掛載時先取出：卸載清理時 ref 可能已被 React 清成 null，但 DOM 節點仍可讀取。
  useEffect(() => {
    const canvas = canvasRef.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (canvas) saveNow(canvas, true);
      else if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [saveNow]);

  const point = (e: PointerEvent<HTMLCanvasElement>, canvas: HTMLCanvasElement) =>
    toCanvasPoint(e, canvas.getBoundingClientRect(), canvas);

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    const ctx = ctxOf(canvas);
    if (!canvas || !ctx || !readyRef.current || dragRef.current) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    canvas.setPointerCapture?.(e.pointerId);
    const p = point(e, canvas);
    const snapshot = readAll(canvas, ctx);
    dragRef.current = { pointerId: e.pointerId, tool, start: p, last: p, moved: false, snapshot };
    if (tool === "brush" || tool === "eraser") {
      drawStroke(ctx, tool, [p], tool === "eraser" ? ERASER_WIDTH : BRUSH_WIDTH);
    }
  };

  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    const canvas = canvasRef.current;
    const ctx = ctxOf(canvas);
    if (!d || !canvas || !ctx || e.pointerId !== d.pointerId) return;
    const p = point(e, canvas);
    if (d.tool === "brush" || d.tool === "eraser") {
      // 逐段畫（上一點 → 這一點），圓頭接頭讓折線連續
      drawStroke(ctx, d.tool, [d.last, p], d.tool === "eraser" ? ERASER_WIDTH : BRUSH_WIDTH);
    } else {
      ctx.putImageData(d.snapshot, 0, 0);
      drawShape(ctx, d.tool, d.start, p, BRUSH_WIDTH);
    }
    d.last = p;
    d.moved = true;
  };

  const onPointerUp = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = dragRef.current;
    const canvas = canvasRef.current;
    const ctx = ctxOf(canvas);
    if (!d || !canvas || !ctx || e.pointerId !== d.pointerId) return;
    dragRef.current = null;
    canvas.releasePointerCapture?.(e.pointerId);
    const isShape = d.tool !== "brush" && d.tool !== "eraser";
    if (isShape && !d.moved) {
      // 只點一下沒拖：形狀不成立，不算一步
      ctx.putImageData(d.snapshot, 0, 0);
      return;
    }
    pushUndo(d.snapshot);
    afterChange(canvas, ctx);
  };

  const undo = () => {
    const canvas = canvasRef.current;
    const ctx = ctxOf(canvas);
    const stack = historyRef.current;
    if (!canvas || !ctx || stack.length === 0 || dragRef.current) return;
    ctx.putImageData(stack[stack.length - 1], 0, 0);
    historyRef.current = stack.slice(0, -1);
    setUndoCount(historyRef.current.length);
    afterChange(canvas, ctx);
  };

  const clear = () => {
    const canvas = canvasRef.current;
    const ctx = ctxOf(canvas);
    if (!canvas || !ctx || !readyRef.current || dragRef.current) return;
    const snapshot = readAll(canvas, ctx);
    if (isBlank(snapshot)) return;
    pushUndo(snapshot);
    fillPaper(ctx, canvas.width, canvas.height);
    afterChange(canvas, ctx);
  };

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const png = await onUploadReference(file);
      const url = URL.createObjectURL(png);
      let img: HTMLImageElement;
      try {
        img = await loadImage(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      const canvas = canvasRef.current;
      const ctx = ctxOf(canvas);
      if (!mountedRef.current || !canvas || !ctx) return;
      // 後端回傳的是 generation_size 的不透明白底線稿：直接蓋上，之後可擦、可復原
      pushUndo(readAll(canvas, ctx));
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      afterChange(canvas, ctx);
    } catch (err) {
      if (mountedRef.current) toast.show(err instanceof Error && err.message ? err.message : "參考圖上傳失敗", "error");
    } finally {
      if (mountedRef.current) setUploading(false);
    }
  };

  return (
    <div className={styles.root}>
      <div role="toolbar" aria-label="畫板工具" className={styles.toolbar}>
        {TOOLS.map((t) => {
          const Icon = Icons[t.icon];
          return (
            <IconButton
              key={t.id}
              label={t.label}
              size={40}
              className={styles.tool}
              active={tool === t.id}
              aria-pressed={tool === t.id}
              onClick={() => setTool(t.id)}
            >
              <Icon />
            </IconButton>
          );
        })}
        <span aria-hidden="true" className={styles.divider} />
        <IconButton
          label="上傳參考圖"
          size={40}
          className={styles.tool}
          disabled={uploading}
          aria-busy={uploading || undefined}
          onClick={() => fileRef.current?.click()}
        >
          <Icons.upload />
        </IconButton>
        <IconButton label="復原" size={40} className={styles.tool} disabled={undoCount === 0} onClick={undo}>
          <Icons.undo />
        </IconButton>
        <IconButton label="清除畫板" size={40} className={styles.tool} onClick={clear}>
          <Icons.trash />
        </IconButton>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          hidden
          tabIndex={-1}
          aria-hidden="true"
          onChange={onFile}
        />
      </div>

      <div className={styles.paper} style={{ aspectRatio: `${width} / ${height}` }}>
        <canvas
          ref={canvasRef}
          width={width}
          height={height}
          className={styles.canvas}
          aria-label="構圖畫板：畫背景與道具，角色位置留白"
          role="img"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        />
        {/*
          參考線比例是相對「內容區」（content_px）。生成尺寸的長寬比與內容區接近但不相等（後端會 letterbox），
          這裡直接把畫布當成內容區來畫：作為構圖參考已足夠，誤差在幾個百分點內。
        */}
        {guides && (
          <>
            <div aria-hidden="true" data-guide="trim" className={`${styles.guide} ${styles.trim}`} style={boxStyle(guides.trim)} />
            <div aria-hidden="true" data-guide="safe" className={`${styles.guide} ${styles.safe}`} style={boxStyle(guides.safe)} />
          </>
        )}
        {blank && <span className={styles.hint}>角色位置留白</span>}
      </div>

      <p className={styles.legend}>
        {guides && (
          <>
            <span>
              <span aria-hidden="true" className={`${styles.swatch} ${styles.swatchTrim}`} /> 裁切線
            </span>
            <span>
              <span aria-hidden="true" className={`${styles.swatch} ${styles.swatchSafe}`} /> 安全區
            </span>
          </>
        )}
        <span>只畫背景與道具</span>
      </p>
    </div>
  );
}

const pct = (v: number) => `${+(v * 100).toFixed(3)}%`;

function boxStyle(b: SketchGuides["trim"]) {
  return { left: pct(b.x), top: pct(b.y), width: pct(b.w), height: pct(b.h) };
}
