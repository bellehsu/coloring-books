"use client";

import { useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import styles from "./Inspector.module.css";
import { sendTracked, sentFor } from "./sentFields";

export const MAX_SCENE = 800;
const STRENGTH_MAX = 0.6;
const STRENGTH_STEP = 0.05;

export interface BackgroundPanelProps {
  /** 呼叫端用 key={page.id}：欄位只在掛載時初始化（sentFields 送出過的值優先，否則用 page）。 */
  page: Page;
  /** PATCH 本頁（scene_prompt／controlnet_strength）；失敗時呼叫端顯示 toast 並 reject。 */
  onPatch: (body: { scene_prompt?: string; controlnet_strength?: number }) => Promise<unknown>;
  /** 產生前的其他寫入（構圖草圖：立即 flush 並等它存好）。reject＝不要產生（呼叫端已顯示訊息）。 */
  beforeGenerate: () => Promise<unknown>;
  /** POST /pages/{id}/backgrounds（一次一張）；失敗時呼叫端顯示 toast，promise 照常 resolve。 */
  onGenerate: () => Promise<unknown>;
  /** POST 進行中。 */
  generating: boolean;
  /** 本頁背景排隊中＋生成中的張數。 */
  activeCount: number;
}

const round2 = (v: number) => Math.round(v * 100) / 100;
const quiet = (p: Promise<unknown>) => p.catch(() => {});

/**
 * 背景模式的面板：場景描述（離開欄位時存）、構圖強度（放開時存）、產生背景（一次一張）。
 * 產生前先 flush 草圖與欄位，全部成功才 POST；整段流程期間停用按鈕。
 */
export function BackgroundPanel({ page, onPatch, beforeGenerate, onGenerate, generating, activeCount }: BackgroundPanelProps) {
  const sceneId = useId();
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
      await onGenerate();
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
          {page.sketch_url ? "越高越貼近構圖草圖；0 表示不參考" : "沒有構圖草圖時不使用"}
        </span>
      </div>
      <Button variant="primary" size="lg" disabled={generating || starting} onClick={() => void generate()}>
        產生背景
      </Button>
      <span className={styles.hintCenter} role="status">
        {activeCount > 0 ? `本頁 ${activeCount} 張排隊或生成中` : "約 10 秒"}
      </span>
      <p className={styles.note2}>先寫好每一頁的場景描述，再按頁面清單上方的『產生所有背景』，可以減少等待</p>
    </section>
  );
}
