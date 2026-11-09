"use client";

import { useId, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import type { Placement } from "@/api/client";
import { cornerResize, movePlacement, placementBox, resizePlacement } from "@/lib/placement";
import styles from "./ComposeStage.module.css";

export interface ComposeStageProps {
  /** 選用背景的圖；沒有時顯示說明（請重新產生）。 */
  backgroundUrl: string | null;
  /**
   * 角色動作的去背圖（GET /generations/{id}/cutout：依遮罩外框裁好的 RGBA PNG，與後端 compose() 同樣的裁切）。
   * 角色框的寬高比取它的實際尺寸，所以預覽與合成結果一致。沒有（缺遮罩）時顯示說明。
   */
  cutoutUrl: string | null;
  /** 目前位置（本 session 最後存的 → 伺服器草稿 → DEFAULT_PLACEMENT）。 */
  placement: Placement;
  /** 結束拖曳或每次按鍵時呼叫一次；EditorPage 會自動存（placementSaver，依序、最新為準）。 */
  onChange: (p: Placement) => void;
  /**
   * 確認合成進行中：不能改位置（拖曳、把手、鍵盤都忽略）。拖到一半時開始確認 → 取消拖曳、
   * 畫面回到已存的位置，也就是合成會用的位置。
   */
  disabled?: boolean;
}

type Size = { w: number; h: number };
/** 每張圖的載入狀態以網址為 key：換圖時自然回到「載入中」。 */
type Loaded = { url: string; size?: Size; error?: boolean };

interface Drag {
  kind: "move" | "resize";
  pointerId: number;
  x: number;
  y: number;
  start: Placement;
  rect: { width: number; height: number };
  draft: Placement;
}

const KEY_STEP = 0.01;
const KEY_STEP_BIG = 0.05;
const KEY_RESIZE = 0.05;

const same = (a: Placement, b: Placement) => a.cx === b.cx && a.cy === b.cy && a.height === b.height && a.flip === b.flip;
const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;

function readSize(img: HTMLImageElement): Size | undefined {
  return img.naturalWidth > 0 && img.naturalHeight > 0 ? { w: img.naturalWidth, h: img.naturalHeight } : undefined;
}

/**
 * 合成模式的舞台：背景圖上疊去背角色。
 * 拖曳角色移動、拖右下角把手等比縮放（左上角固定）；角色框可聚焦：方向鍵移動 1%、Shift＋方向鍵 5%、+／- 縮放 5%。
 * 位置用百分比擺放，舞台寬度跟著容器（手機寬也一樣）；超出背景的部分裁掉（與合成結果相同）。
 */
export function ComposeStage({ backgroundUrl, cutoutUrl, placement, onChange, disabled = false }: ComposeStageProps) {
  const hintId = useId();
  const [bg, setBg] = useState<Loaded | null>(null);
  const [cut, setCut] = useState<Loaded | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [overlay, setOverlay] = useState<HTMLDivElement | null>(null);

  // 確認合成開始時取消進行中的拖曳（之後放開也不送），畫面回到已存的位置
  if (disabled && drag) setDrag(null);

  if (!backgroundUrl) return <Msg>背景圖片無法載入，請重新產生</Msg>;
  if (!cutoutUrl) return <Msg>角色動作缺少去背遮罩，請重新產生</Msg>;
  const bgState = bg?.url === backgroundUrl ? bg : null;
  const cutState = cut?.url === cutoutUrl ? cut : null;
  if (bgState?.error) return <Msg>背景圖片無法載入，請重新產生</Msg>;
  if (cutState?.error) return <Msg>角色去背圖無法載入，請重新產生</Msg>;

  const bgSize = bgState?.size;
  const cutSize = cutState?.size;
  const ready = bgSize != null && cutSize != null;
  const aspect = cutSize ? cutSize.w / cutSize.h : 1;
  /** 角色框寬（相對背景寬）／高（相對背景高）。 */
  const k = bgSize ? (aspect * bgSize.h) / bgSize.w : aspect;
  const shown = drag?.draft ?? placement;

  const draftOf = (d: Drag, e: PointerEvent<HTMLElement>) => {
    const dx = (e.clientX - d.x) / d.rect.width;
    const dy = (e.clientY - d.y) / d.rect.height;
    return d.kind === "move" ? movePlacement(d.start, dx, dy) : cornerResize(d.start, k, dx, dy);
  };

  const begin = (kind: Drag["kind"]) => (e: PointerEvent<HTMLElement>) => {
    if (disabled || !ready || drag) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    const rect = overlay?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    if (kind === "move") e.currentTarget.focus();
    setDrag({ kind, pointerId: e.pointerId, x: e.clientX, y: e.clientY, start: placement, rect, draft: placement });
  };
  const move = (e: PointerEvent<HTMLElement>) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    setDrag({ ...drag, draft: draftOf(drag, e) });
  };
  const end = (e: PointerEvent<HTMLElement>) => {
    if (!drag || e.pointerId !== drag.pointerId) return;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    const next = draftOf(drag, e);
    setDrag(null);
    if (!same(next, drag.start)) onChange(next);
  };
  const cancel = (e: PointerEvent<HTMLElement>) => {
    if (drag && e.pointerId === drag.pointerId) setDrag(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (disabled || !ready || drag) return;
    // Ctrl／⌘＋−／＝ 是瀏覽器縮放，不要吃掉
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const step = e.shiftKey ? KEY_STEP_BIG : KEY_STEP;
    let next: Placement | null = null;
    switch (e.key) {
      case "ArrowLeft":
        next = movePlacement(placement, -step, 0);
        break;
      case "ArrowRight":
        next = movePlacement(placement, step, 0);
        break;
      case "ArrowUp":
        next = movePlacement(placement, 0, -step);
        break;
      case "ArrowDown":
        next = movePlacement(placement, 0, step);
        break;
      case "+":
      case "=":
        next = resizePlacement(placement, KEY_RESIZE);
        break;
      case "-":
      case "_":
        next = resizePlacement(placement, -KEY_RESIZE);
        break;
    }
    if (!next) return;
    e.preventDefault();
    if (!same(next, placement)) onChange(next);
  };

  // 背景像素座標下的角色框 → 百分比（與後端同樣以背景尺寸計算）
  const bw = bgSize?.w ?? 1;
  const bh = bgSize?.h ?? 1;
  const b = placementBox(shown, aspect, bw, bh);
  const boxStyle = ready
    ? { left: pct(b.left / bw), top: pct(b.top / bh), width: pct(b.width / bw), height: pct(b.height / bh) }
    : undefined;
  // 把手在角色框右下角；框超出背景時停在背景邊緣，才拉得到
  const handleStyle = ready
    ? { left: pct(Math.min(b.left + b.width, bw) / bw), top: pct(Math.min(b.top + b.height, bh) / bh) }
    : undefined;

  return (
    <div className={styles.wrap}>
      <div
        className={styles.frame}
        data-ready={ready}
        style={bgSize ? ({ aspectRatio: `${bgSize.w} / ${bgSize.h}`, "--bg-aspect": bgSize.w / bgSize.h } as CSSProperties) : undefined}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={backgroundUrl}
          alt="背景圖"
          className={styles.bg}
          draggable={false}
          onLoad={(e) => setBg({ url: backgroundUrl, size: readSize(e.currentTarget) })}
          onError={() => setBg({ url: backgroundUrl, error: true })}
        />
        <div ref={setOverlay} className={styles.overlay} data-testid="compose-overlay">
          <div
            role="group"
            aria-roledescription="可移動的角色"
            aria-label="角色位置"
            aria-describedby={hintId}
            aria-disabled={disabled || undefined}
            tabIndex={0}
            className={styles.box}
            data-ready={ready}
            data-dragging={drag?.kind === "move"}
            style={boxStyle}
            onPointerDown={begin("move")}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={cancel}
            onLostPointerCapture={cancel}
            onKeyDown={onKeyDown}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={cutoutUrl}
              alt=""
              data-testid="cutout"
              data-flip={shown.flip}
              className={styles.cutout}
              draggable={false}
              onLoad={(e) => setCut({ url: cutoutUrl, size: readSize(e.currentTarget) })}
              onError={() => setCut({ url: cutoutUrl, error: true })}
            />
          </div>
          {ready && (
            <div
              aria-hidden="true"
              data-testid="resize-handle"
              className={styles.handle}
              data-disabled={disabled}
              style={handleStyle}
              onPointerDown={begin("resize")}
              onPointerMove={move}
              onPointerUp={end}
              onPointerCancel={cancel}
            onLostPointerCapture={cancel}
            />
          )}
        </div>
        {!ready && (
          <p className={styles.loading} aria-busy="true">
            載入中…
          </p>
        )}
      </div>
      <p id={hintId} className={styles.hint}>
        拖曳角色移動，拖右下角把手縮放；也可以選取角色後用方向鍵移動（Shift 一次 5%）、＋／－ 縮放
      </p>
    </div>
  );
}

function Msg({ children }: { children: string }) {
  return (
    <p role="alert" className={styles.msg}>
      {children}
    </p>
  );
}
