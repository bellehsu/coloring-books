"use client";

import { useId, useRef, useState } from "react";
import Link from "next/link";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Segmented } from "@/components/ui/Segmented";
import { ACTIONS, hasPhrase, appendPhrase } from "@/lib/actions";
import type { PoseMode } from "@/lib/poseMode";
import styles from "./Inspector.module.css";

export const MAX_ACTION = 300;

const MODE_HINT: Record<PoseMode, string> = {
  precise: "約 30 秒，角色最像、動作最準",
  fast: "約 10 秒，可能和角色有落差",
};

export interface PosePanelProps {
  /** 呼叫端用 key={page.id}。 */
  page: Page;
  /** 本書已設定角色；false 時停用並連到角色工作室。 */
  hasCharacter: boolean;
  characterHref: string;
  /** POST /pages/{id}/poses {action, mode}（一次一張）；失敗時呼叫端顯示 toast，promise 照常 resolve。 */
  onGenerate: (action: string, mode: PoseMode) => Promise<unknown>;
  /** POST 進行中。 */
  generating: boolean;
  /** 本頁角色動作排隊中＋生成中的張數。 */
  activeCount: number;
  /** 目前的產生方式（呼叫端已套用「精準不可用 → 快速」）。 */
  mode: PoseMode;
  /** 使用者切換產生方式；呼叫端負責記住（localStorage）。 */
  onModeChange: (m: PoseMode) => void;
  /** 精準模式不可用的原因；有值時停用「精準」並顯示。 */
  preciseUnavailableReason: string | null;
}

/** 角色模式的面板：動作描述（1–300 字）、十個常用動作、產生方式（精準｜快速）、產生角色動作（一次一張，整段停用防連點）。 */
export function PosePanel({
  page,
  hasCharacter,
  characterHref,
  onGenerate,
  generating,
  activeCount,
  mode,
  onModeChange,
  preciseUnavailableReason,
}: PosePanelProps) {
  const id = useId();
  const [action, setAction] = useState("");
  const [starting, setStarting] = useState(false);
  const startingRef = useRef(false);
  const trimmed = action.trim();

  async function generate() {
    if (startingRef.current || !trimmed) return;
    startingRef.current = true;
    setStarting(true);
    try {
      await onGenerate(trimmed, mode);
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }

  return (
    <section aria-labelledby={`${id}-h`} className={styles.panel} data-page={page.id}>
      <h2 id={`${id}-h`} className={styles.h2}>
        角色動作
      </h2>
      <div className={styles.field}>
        <label htmlFor={id}>動作描述</label>
        <textarea
          id={id}
          rows={3}
          maxLength={MAX_ACTION}
          className={styles.textarea}
          value={action}
          placeholder="例如：waving one hand"
          aria-describedby={`${id}-count`}
          onChange={(e) => setAction(e.target.value)}
        />
        <span id={`${id}-count`} className={styles.counter}>
          {action.length} / {MAX_ACTION}
        </span>
      </div>
      <div className={styles.presets} role="group" aria-label="常用動作">
        {ACTIONS.map((a) => {
          const added = hasPhrase(action, a.phrase);
          const tooLong = !added && appendPhrase(action, a.phrase).length > MAX_ACTION;
          return (
            <button
              key={a.phrase}
              type="button"
              className={styles.preset}
              data-added={added}
              disabled={added || tooLong}
              aria-label={added ? `${a.label}（已加入）` : undefined}
              title={tooLong ? `再加入會超過 ${MAX_ACTION} 字` : undefined}
              onClick={() => setAction((cur) => appendPhrase(cur, a.phrase))}
            >
              {added ? <span aria-hidden="true">✓ </span> : null}
              {a.label}
            </button>
          );
        })}
      </div>
      <div className={styles.field}>
        <Segmented
          kind="radio"
          label="產生方式"
          value={mode}
          onChange={onModeChange}
          describedBy={preciseUnavailableReason ? `${id}-mode ${id}-why` : `${id}-mode`}
          options={[
            { value: "precise", label: "精準", disabled: preciseUnavailableReason !== null },
            { value: "fast", label: "快速" },
          ]}
        />
        <span id={`${id}-mode`} className={styles.hint}>
          {MODE_HINT[mode]}
        </span>
        {preciseUnavailableReason && (
          <span id={`${id}-why`} className={styles.warnHint}>
            {preciseUnavailableReason}
          </span>
        )}
      </div>
      <Button variant="primary" size="lg" disabled={!hasCharacter || !trimmed || generating || starting} onClick={() => void generate()}>
        產生角色動作
      </Button>
      {hasCharacter ? (
        <span className={styles.hintCenter} role="status">
          {activeCount > 0 ? `本頁 ${activeCount} 張排隊或生成中` : null}
        </span>
      ) : (
        <span className={styles.hintCenter}>
          <Link href={characterHref}>先設定角色</Link>
        </span>
      )}
    </section>
  );
}
