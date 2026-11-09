"use client";

import { useId, useRef, useState } from "react";
import Link from "next/link";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import styles from "./Inspector.module.css";

export const MAX_SCENE = 800;
const STRENGTH_MAX = 0.6;
const STRENGTH_STEP = 0.05;

type PagePatch = { scene_prompt?: string; controlnet_strength?: number };

export interface GeneratePanelProps {
  /** 呼叫端請用 key={page.id}：欄位只在掛載時從 page 初始化。 */
  page: Page;
  hasCharacter: boolean;
  characterHref: string;
  /** PATCH 本頁；失敗時呼叫端顯示 toast 並 reject。 */
  onPatch: (body: PagePatch) => Promise<unknown>;
  /** POST candidates {count: 4}；失敗時呼叫端顯示 toast。 */
  onGenerate: () => Promise<unknown>;
  generating: boolean;
  /** 本頁排隊中＋生成中的張數。 */
  activeCount: number;
}

const round2 = (v: number) => Math.round(v * 100) / 100;

/** 構圖模式的生成設定：場景描述（離開欄位時存）、構圖強度（放開時存）、產生 4 張候選。 */
export function GeneratePanel({
  page,
  hasCharacter,
  characterHref,
  onPatch,
  onGenerate,
  generating,
  activeCount,
}: GeneratePanelProps) {
  const sceneId = useId();
  const strengthId = useId();
  const [scene, setScene] = useState(page.scene_prompt);
  const [strength, setStrength] = useState(page.controlnet_strength);
  /** 最後送出的值與它的請求（產生前要等它完成；同一個值不重送）。 */
  const sentScene = useRef<{ value: string; p: Promise<unknown> } | null>(null);
  const sentStrength = useRef<{ value: number; p: Promise<unknown> } | null>(null);

  function saveScene(): Promise<unknown> {
    const last = sentScene.current;
    if (last?.value === scene) return last.p;
    if (!last && scene === page.scene_prompt) return Promise.resolve();
    const p = onPatch({ scene_prompt: scene });
    sentScene.current = { value: scene, p };
    return p;
  }

  function saveStrength(): Promise<unknown> {
    const v = round2(strength);
    const last = sentStrength.current;
    if (last?.value === v) return last.p;
    if (!last && v === round2(page.controlnet_strength)) return Promise.resolve();
    const p = onPatch({ controlnet_strength: v });
    sentStrength.current = { value: v, p };
    return p;
  }

  const quiet = (p: Promise<unknown>) => p.catch(() => {});

  async function generate() {
    try {
      await Promise.all([saveScene(), saveStrength()]);
    } catch {
      return; // 存檔失敗已顯示訊息；不要用舊描述生成
    }
    await onGenerate();
  }

  return (
    <section aria-labelledby={`${sceneId}-h`} className={styles.panel}>
      <h2 id={`${sceneId}-h`} className={styles.h2}>
        生成
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
      <Button variant="primary" size="lg" disabled={!hasCharacter || generating} onClick={() => void generate()}>
        產生 4 張候選
      </Button>
      {hasCharacter ? (
        <span className={styles.hintCenter} role="status">
          {activeCount > 0 ? `本頁 ${activeCount} 張排隊或生成中` : "每張約 8–13 秒"}
        </span>
      ) : (
        <span className={styles.hintCenter}>
          還沒設定角色，不能產生。<Link href={characterHref}>前往角色工作室</Link>
        </span>
      )}
    </section>
  );
}
