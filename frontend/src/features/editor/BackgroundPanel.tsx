"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { effectiveBgStyle, type BgStyle } from "@/lib/bgStyle";
import styles from "./Inspector.module.css";
import { sendTracked, sentFor } from "./sentFields";

export const MAX_SCENE = 800;
const STRENGTH_MAX = 0.6;
const STRENGTH_STEP = 0.05;

const STYLE_HINT: Record<BgStyle, string> = {
  simple: "約 20 秒，線條少、適合小小孩",
  detailed: "約 10 秒，細節多，可以照構圖草圖",
};
const SKETCH_NOTE = "有構圖草圖時用細緻，才會照草圖構圖";

export interface BackgroundPanelProps {
  /** 呼叫端用 key={page.id}：欄位只在掛載時初始化（sentFields 送出過的值優先，否則用 page）。 */
  page: Page;
  /** PATCH 本頁（scene_prompt／controlnet_strength）；失敗時呼叫端顯示 toast 並 reject。 */
  onPatch: (body: { scene_prompt?: string; controlnet_strength?: number }) => Promise<unknown>;
  /** 產生前的其他寫入（構圖草圖：立即 flush 並等它存好）。reject＝不要產生（呼叫端已顯示訊息）。 */
  beforeGenerate: () => Promise<unknown>;
  /**
   * POST /pages/{id}/backgrounds {style}（一次一張）；失敗時呼叫端顯示 toast，promise 照常 resolve。
   * style＝使用者選的（只套用可用性）；有草圖改用細緻由後端決定。
   */
  onGenerate: (style: BgStyle) => Promise<unknown>;
  /** POST 進行中。 */
  generating: boolean;
  /** 本頁背景排隊中＋生成中的張數。 */
  activeCount: number;
  /** 使用者記住的背景風格（未套用草圖／可用性）。 */
  style: BgStyle;
  /** 使用者切換風格；呼叫端負責記住（localStorage）。 */
  onStyleChange: (s: BgStyle) => void;
  /** 簡潔不可用的原因；有值時停用「簡潔」並顯示、改用細緻。 */
  simpleUnavailableReason: string | null;
  /**
   * 本頁有沒有構圖草圖。預設看 page.sketch_url；呼叫端可傳更即時的值（這個 session 剛送出的草圖，
   * 不等 PUT 與 pages 重抓）。
   */
  hasSketch?: boolean;
}

const round2 = (v: number) => Math.round(v * 100) / 100;
const quiet = (p: Promise<unknown>) => p.catch(() => {});

/**
 * 背景模式的面板：場景描述（離開欄位時存）、構圖強度（放開時存）、背景風格（簡潔｜細緻）、產生背景（一次一張）。
 * 產生前先 flush 草圖與欄位，全部成功才 POST；整段流程期間停用按鈕。
 */
export function BackgroundPanel({
  page,
  onPatch,
  beforeGenerate,
  onGenerate,
  generating,
  activeCount,
  style,
  onStyleChange,
  simpleUnavailableReason,
  hasSketch = page.sketch_url != null,
}: BackgroundPanelProps) {
  const sceneId = useId();
  const styleId = useId();
  const strengthId = useId();
  const qc = useQueryClient();
  // 最後送出的值與它的請求（產生前要等它完成；同一個值不重送）；跨重新掛載保留，見 sentFields。
  const [scene, setScene] = useState(() => sentFor(qc, page.id).scene?.value ?? page.scene_prompt);
  const [strength, setStrength] = useState(() => sentFor(qc, page.id).strength?.value ?? page.controlnet_strength);

  function saveScene(): Promise<unknown> {
    const last = sentFor(qc, page.id).scene;
    if (last?.value === scene) return last.p;
    if (!last && scene === page.scene_prompt) return Promise.resolve();
    return sendTracked(qc, page.id, "scene", scene, () => onPatch({ scene_prompt: scene }));
  }

  function saveStrength(): Promise<unknown> {
    const v = round2(strength);
    const last = sentFor(qc, page.id).strength;
    if (last?.value === v) return last.p;
    if (!last && v === round2(page.controlnet_strength)) return Promise.resolve();
    return sendTracked(qc, page.id, "strength", v, () => onPatch({ controlnet_strength: v }));
  }

  // 有可用的構圖草圖（有草圖且滑桿 > 0，拖曳中即時反應）→ 細緻；簡潔不可用 → 細緻。不改寫記住的風格。
  const sketchForced = hasSketch && strength > 0;
  const effective = effectiveBgStyle(style, {
    hasSketch: sketchForced,
    simpleAvailable: simpleUnavailableReason === null,
  });
  const simpleDisabled = sketchForced || simpleUnavailableReason !== null;
  const describedBy = [
    `${styleId}-hint`,
    sketchForced && `${styleId}-sketch`,
    simpleUnavailableReason && `${styleId}-why`,
  ]
    .filter(Boolean)
    .join(" ");

  // 送出的風格＝使用者選的，只套用可用性（不套用草圖：後端依草圖決定實際風格），
  // 所以 background_style_requested 是使用者的選擇。
  const requested = effectiveBgStyle(style, {
    hasSketch: false,
    simpleAvailable: simpleUnavailableReason === null,
  });

  // 產生時在存檔（含草圖 flush）之後才讀：等待期間簡潔變成不可用就送細緻
  const requestedRef = useRef(requested);
  useEffect(() => {
    requestedRef.current = requested;
  });

  // 整段流程（等存檔 → POST）期間停用按鈕；ref 擋住同一輪 render 前的第二次點擊。
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);

  async function generate() {
    if (startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    try {
      try {
        await Promise.all([saveScene(), saveStrength(), beforeGenerate()]);
      } catch {
        return; // 存檔失敗已顯示訊息；不要用舊描述或舊草圖生成
      }
      await onGenerate(requestedRef.current);
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }

  return (
    <section aria-labelledby={`${sceneId}-h`} className={styles.panel} data-page={page.id}>
      <h2 id={`${sceneId}-h`} className={styles.h2}>
        背景
      </h2>
      <div className={styles.field}>
        <label htmlFor={sceneId}>場景描述</label>
        <textarea
          id={sceneId}
          rows={3}
          maxLength={MAX_SCENE}
          className={styles.textarea}
          value={scene}
          placeholder="例如：in a cozy kitchen baking cookies"
          aria-describedby={`${sceneId}-count`}
          onChange={(e) => setScene(e.target.value)}
          onBlur={() => quiet(saveScene())}
        />
        <span id={`${sceneId}-count`} className={styles.counter}>
          {scene.length} / {MAX_SCENE}
        </span>
      </div>
      <div className={styles.field}>
        <label htmlFor={strengthId} className={styles.rangeLabel}>
          構圖強度 <span className={styles.value}>{strength.toFixed(2)}</span>
        </label>
        <input
          id={strengthId}
          type="range"
          min={0}
          max={STRENGTH_MAX}
          step={STRENGTH_STEP}
          value={strength}
          className={styles.range}
          aria-describedby={`${strengthId}-hint`}
          onChange={(e) => setStrength(Number(e.target.value))}
          onPointerUp={() => quiet(saveStrength())}
          onKeyUp={() => quiet(saveStrength())}
          onBlur={() => quiet(saveStrength())}
        />
        <span id={`${strengthId}-hint`} className={styles.hint}>
          {hasSketch ? "越高越貼近構圖草圖；0 表示不參考" : "沒有構圖草圖時不使用"}
        </span>
      </div>
      <div className={styles.field}>
        <Segmented
          kind="radio"
          label="背景風格"
          value={effective}
          onChange={onStyleChange}
          describedBy={describedBy}
          options={[
            { value: "simple", label: "簡潔", disabled: simpleDisabled },
            { value: "detailed", label: "細緻" },
          ]}
        />
        <span id={`${styleId}-hint`} className={styles.hint}>
          {STYLE_HINT[effective]}
        </span>
        {sketchForced && (
          <span id={`${styleId}-sketch`} className={styles.hint}>
            {SKETCH_NOTE}
          </span>
        )}
        {simpleUnavailableReason && (
          <span id={`${styleId}-why`} className={styles.warnHint}>
            {simpleUnavailableReason}
          </span>
        )}
      </div>
      <Button variant="primary" size="lg" disabled={generating || starting} onClick={() => void generate()}>
        產生背景
      </Button>
      <span className={styles.hintCenter} role="status">
        {activeCount > 0 ? `本頁 ${activeCount} 張排隊或生成中` : null}
      </span>
    </section>
  );
}
