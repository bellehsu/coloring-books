"use client";

import {
  useId,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { DEFAULT_PLACEMENT, type Placement } from "@/api/client";
import {
  clampPlacement,
  cornerResize,
  currentSide,
  currentSize,
  movePlacement,
  placementBox,
  resizePlacement,
  withSide,
  withSize,
  type PresetSide,
  type PresetSize,
} from "@/lib/placement";
import { errMsg } from "./errMsg";
import stage from "./stage.module.css";
import styles from "./ComposeStage.module.css";

/** 自動合成的狀態（進階編輯的 autoCompose 回報，ComposeStatus 顯示）。 */
export type AutoComposeStatus = "idle" | "pending" | "running" | "error";

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
  /**
   * 結束拖曳、縮放，每次按鍵，或按位置／大小快捷鈕、「水平翻轉」「重設位置」時呼叫一次；
   * EditorPage 會自動存（placementSaver，依序、最新為準）並排自動合成。
   * via＝"key"：方向鍵／＋－（按住會自動重複，C 等停下來才存）。
   */
  onChange: (p: Placement, via?: "key") => void;
  /** 舞台下方、翻轉／重設旁的狀態（自動合成、位置存檔失敗）。 */
  status?: ReactNode;
  /**
   * 「放進框裡（肖像）」：算好的肖像位置（POST /pages/{id}/frame-fit，不存）；null＝這張背景找不到框。
   * 沒給（背景或角色動作還沒選好）時不顯示按鈕。結果經 onChange 存檔＋自動合成。
   */
  onFrameFit?: () => Promise<Placement | null>;
  /**
   * 肖像開著時背景的框內遮罩（GET /generations/{id}/frame-mask，與背景同尺寸，白＝框內），角色圖層用它裁切預覽。
   * 載入失敗（404＝這張背景找不到框）→ 不裁，狀態列說明。
   */
  frameMaskUrl?: string | null;
}

type Size = { w: number; h: number };
/** 每張圖的載入狀態以網址為 key：換圖時自然回到「載入中」。 */
type Loaded = { url: string; size?: Size; error?: boolean };
/** 「放進框裡」的結果說明，以背景網址為 key：換背景就不再顯示。 */
type FitNote = { url: string; text: string };

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

const SIDES: [PresetSide, string][] = [["left", "靠左"], ["center", "置中"], ["right", "靠右"]];
const SIZES: [PresetSize, string][] = [["small", "小"], ["medium", "中"], ["large", "大"]];

/** 找不到框（frame-fit 422、frame-mask 404）時狀態列的說明（spec §5.5）。 */
const NO_FRAME = "這張背景找不到框，照一般擺放";
const HINT =
  "拖曳角色移動，拉右下角圓點縮放；也可以按下方的「位置」「大小」按鈕快速擺放，或選取角色後用方向鍵移動（Shift 一次 5%）、＋／－ 縮放。放開後自動合成，不用另外按確認。";
const HINT_PORTRAIT = "肖像：主角自動放大，框外的部分裁掉。可以拖曳或拉圓點微調；再按一次『放進框裡』關掉。";

const same = (a: Placement, b: Placement) =>
  a.cx === b.cx &&
  a.cy === b.cy &&
  a.height === b.height &&
  a.flip === b.flip &&
  (a.frame?.x ?? null) === (b.frame?.x ?? null) &&
  (a.frame?.y ?? null) === (b.frame?.y ?? null) &&
  !!a.autofit === !!b.autofit;
const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;

function readSize(img: HTMLImageElement): Size | undefined {
  return img.naturalWidth > 0 && img.naturalHeight > 0 ? { w: img.naturalWidth, h: img.naturalHeight } : undefined;
}

/**
 * 合成模式的舞台：背景圖上疊去背角色，下方是位置／大小快捷鈕、「水平翻轉」「重設位置」與狀態。
 * 拖曳角色移動、拖右下角把手等比縮放（左上角固定）；角色框可聚焦：方向鍵移動 1%、Shift＋方向鍵 5%、+／- 縮放 5%。
 * 下方快捷鈕：位置（靠左／置中／靠右）與大小（小／中／大），見 lib/placement 的 withSide／withSize。
 * 位置用百分比擺放，舞台寬度跟著容器（手機寬也一樣）；超出背景的部分裁掉（與合成結果相同）。
 * 「放進框裡（肖像）」（spec 2026-11-24-portrait-frame-fit §5.5）：placement.frame 不是 null＝肖像開著；
 * 去背圖在一個與舞台同大小的圖層裡，套上框內遮罩裁切（角色框、圓點把手不在圖層裡，不會被裁掉）；
 * 肖像開著時位置／大小快捷鈕停用，拖曳、圓點、方向鍵、＋／－ 照常但都送 autofit: false（保留 frame）。
 */
export function ComposeStage({
  backgroundUrl,
  cutoutUrl,
  placement,
  onChange,
  status,
  onFrameFit,
  frameMaskUrl,
}: ComposeStageProps) {
  const hintId = useId();
  const [bg, setBg] = useState<Loaded | null>(null);
  const [cut, setCut] = useState<Loaded | null>(null);
  const [mask, setMask] = useState<Loaded | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [overlay, setOverlay] = useState<HTMLDivElement | null>(null);
  const [fitting, setFitting] = useState(false);
  const [fitNote, setFitNote] = useState<FitNote | null>(null);

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
  const portrait = placement.frame != null;
  const maskState = portrait && frameMaskUrl && mask?.url === frameMaskUrl ? mask : null;
  /** 遮罩載入好了才裁（還沒載入或載入失敗都不裁）。 */
  const masked = maskState?.size != null;
  const note = maskState?.error ? NO_FRAME : fitNote?.url === backgroundUrl ? fitNote.text : null;
  /** 肖像開著的手動調整：送 autofit: false，伺服器合成時不再依框重新對齊（frame 保留）。 */
  const manual = (p: Placement): Placement => (p.frame ? { ...p, autofit: false } : p);

  const draftOf = (d: Drag, e: PointerEvent<HTMLElement>) => {
    const dx = (e.clientX - d.x) / d.rect.width;
    const dy = (e.clientY - d.y) / d.rect.height;
    return d.kind === "move" ? movePlacement(d.start, dx, dy) : cornerResize(d.start, k, dx, dy);
  };

  const begin = (kind: Drag["kind"]) => (e: PointerEvent<HTMLElement>) => {
    if (!ready || drag) return;
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
    if (!same(next, drag.start)) onChange(manual(next));
  };
  const cancel = (e: PointerEvent<HTMLElement>) => {
    if (drag && e.pointerId === drag.pointerId) setDrag(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (!ready || drag) return;
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
    if (!same(next, placement)) onChange(manual(next), "key");
  };

  // 背景像素座標下的角色框 → 百分比（與後端同樣以背景尺寸計算）
  const bw = bgSize?.w ?? 1;
  const bh = bgSize?.h ?? 1;
  const b = placementBox(shown, aspect, bw, bh);
  /** 按鈕：拖曳中（觸控時另一隻手指）不作用，否則放開時拖曳會蓋掉；結果與目前相同就不送（不重複存檔／合成）。 */
  const apply = (next: Placement) => {
    if (drag) return;
    if (!same(next, placement)) onChange(next);
  };
  /** 「放進框裡」：關 → 開 找框並套用；開 → 關 回到預設位置（保留翻轉，frame／autofit 清掉）。 */
  const togglePortrait = async () => {
    if (drag || fitting || !onFrameFit) return;
    if (portrait) {
      apply({ ...DEFAULT_PLACEMENT, flip: placement.flip });
      return;
    }
    const url = backgroundUrl;
    setFitNote(null);
    setFitting(true);
    try {
      const fit = await onFrameFit();
      if (fit) onChange(clampPlacement({ ...fit, flip: placement.flip }));
      else setFitNote({ url, text: NO_FRAME });
    } catch (e) {
      setFitNote({ url, text: errMsg(e, "找框失敗，請再試一次") });
    } finally {
      setFitting(false);
    }
  };
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
          {/* 只放去背圖的圖層（與舞台同大小）：肖像時套框內遮罩，遮罩與背景對齊 */}
          <div
            className={styles.cutLayer}
            data-testid="cutout-layer"
            data-masked={masked}
            style={masked ? ({ "--frame-mask": `url("${frameMaskUrl}")` } as CSSProperties) : undefined}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={cutoutUrl}
              alt=""
              data-testid="cutout"
              data-flip={shown.flip}
              data-ready={ready}
              className={styles.cutout}
              style={boxStyle}
              draggable={false}
              onLoad={(e) => setCut({ url: cutoutUrl, size: readSize(e.currentTarget) })}
              onError={() => setCut({ url: cutoutUrl, error: true })}
            />
          </div>
          <div
            role="group"
            aria-roledescription="可移動的角色"
            aria-label="角色位置"
            aria-describedby={hintId}
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
          />
          {ready && (
            <div
              aria-hidden="true"
              data-testid="resize-handle"
              className={styles.handle}
              style={handleStyle}
              onPointerDown={begin("resize")}
              onPointerMove={move}
              onPointerUp={end}
              onPointerCancel={cancel}
              onLostPointerCapture={cancel}
            />
          )}
        </div>
        {portrait && frameMaskUrl && (
          // 先載入遮罩：成功才裁（CSS mask 載入失敗會把角色整個遮掉），404＝這張背景找不到框
          // eslint-disable-next-line @next/next/no-img-element
          <img
            hidden
            src={frameMaskUrl}
            alt=""
            data-testid="frame-mask-probe"
            onLoad={(e) => setMask({ url: frameMaskUrl, size: readSize(e.currentTarget) ?? { w: 1, h: 1 } })}
            onError={() => setMask({ url: frameMaskUrl, error: true })}
          />
        )}
        {!ready && (
          <p className={styles.loading} aria-busy="true">
            載入中…
          </p>
        )}
      </div>
      <div className={styles.presets}>
        <PresetGroup
          label="位置快捷鈕"
          title="位置"
          options={SIDES}
          current={currentSide(placement)}
          disabled={portrait}
          onPick={(side) => apply(withSide(placement, side))}
        />
        <PresetGroup
          label="大小快捷鈕"
          title="大小"
          options={SIZES}
          current={currentSize(placement)}
          disabled={portrait}
          onPick={(size) => apply(withSize(placement, size))}
        />
      </div>
      <div className={styles.bar}>
        <button
          type="button"
          className={styles.mini}
          aria-pressed={placement.flip}
          onClick={() => onChange({ ...placement, flip: !placement.flip })}
        >
          水平翻轉
        </button>
        <button
          type="button"
          className={styles.mini}
          onClick={() => apply({ ...DEFAULT_PLACEMENT, flip: placement.flip })}
        >
          重設位置
        </button>
        {onFrameFit && (
          <>
            <button
              type="button"
              className={styles.mini}
              aria-pressed={portrait}
              disabled={fitting}
              aria-busy={fitting}
              onClick={() => void togglePortrait()}
            >
              放進框裡（肖像）
            </button>
            <span role="status" data-testid="portrait-status" className={styles.status}>
              {note}
            </span>
          </>
        )}
        {status}
      </div>
      <p id={hintId} className={styles.hint}>
        {portrait ? HINT_PORTRAIT : HINT}
      </p>
    </div>
  );
}

/**
 * 背景或角色動作還沒選好（或還在產生）時的舞台：有什麼就顯示什麼，加上一行說明。
 * 只有背景 → 整張背景；只有角色動作 → 去背角色放在紙上。
 */
export function ComposePreview({
  backgroundUrl,
  cutoutUrl,
  aspect,
  message,
}: {
  backgroundUrl?: string | null;
  cutoutUrl?: string | null;
  /** 紙張（生成尺寸）寬／高：只有角色動作時用。 */
  aspect: number;
  message: string;
}) {
  return (
    <div className={styles.wrap}>
      <div
        className={styles.frame}
        data-ready="true"
        style={{ aspectRatio: String(aspect), "--bg-aspect": aspect } as CSSProperties}
      >
        {backgroundUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={backgroundUrl} alt="背景圖" className={styles.bg} draggable={false} />
        ) : cutoutUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={cutoutUrl} alt="角色動作" className={styles.lonePose} draggable={false} />
        ) : null}
      </div>
      <p role="status" className={styles.waiting}>
        {message}
      </p>
    </div>
  );
}

/**
 * 舞台下方的狀態：位置存檔失敗（持續顯示＋重試，toast 會消失）與自動合成
 * （「更新中…」／「更新失敗，重試」按鈕／「✓ 線稿已自動更新」）。
 */
export function ComposeStatus({
  status,
  upToDate,
  onRetry,
  placementFailed,
  onRetryPlacement,
}: {
  status: AutoComposeStatus;
  /** 閒置時，選定圖就是目前這組背景＋角色動作＋位置的合成圖。 */
  upToDate: boolean;
  onRetry: () => void;
  placementFailed: boolean;
  onRetryPlacement: () => void;
}) {
  return (
    <>
      {placementFailed && (
        <span role="alert" data-testid="placement-save-failed" className={`${stage.saveFailed} ${stage.placementSaveFailed}`}>
          位置尚未儲存
          <button type="button" className={stage.retry} onClick={onRetryPlacement}>
            重試
          </button>
        </span>
      )}
      <span role="status" className={styles.status} data-status={status}>
        {status === "pending" || status === "running" ? (
          "更新中…"
        ) : status === "error" ? (
          <button type="button" className={`${styles.mini} ${styles.retry}`} onClick={onRetry}>
            更新失敗，重試
          </button>
        ) : upToDate ? (
          "✓ 線稿已自動更新"
        ) : null}
      </span>
    </>
  );
}

/** 一組三選一快捷鈕（位置或大小）；符合目前位置的那顆 aria-pressed。 */
function PresetGroup<K extends string>({
  label,
  title,
  options,
  current,
  disabled = false,
  onPick,
}: {
  label: string;
  title: string;
  options: [K, string][];
  current: K | null;
  /** 肖像開著時停用（位置由框決定）。 */
  disabled?: boolean;
  onPick: (k: K) => void;
}) {
  return (
    <div role="group" aria-label={label} className={styles.presetGroup}>
      <span className={styles.presetTitle} aria-hidden="true">
        {title}
      </span>
      {options.map(([k, text]) => (
        <button
          key={k}
          type="button"
          className={styles.mini}
          aria-pressed={current === k}
          disabled={disabled}
          onClick={() => onPick(k)}
        >
          {text}
        </button>
      ))}
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
