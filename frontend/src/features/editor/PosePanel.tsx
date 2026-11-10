"use client";

import { useId, useRef, useState } from "react";
import Link from "next/link";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { ACTIONS, hasPhrase, appendPhrase } from "@/lib/actions";
import styles from "./Inspector.module.css";

export const MAX_ACTION = 300;

export interface PosePanelProps {
  /** 呼叫端用 key={page.id}。 */
  page: Page;
  /** 本書已設定角色；false 時停用並連到角色工作室。 */
  hasCharacter: boolean;
  characterHref: string;
  /** POST /pages/{id}/poses {action}（一次一張）；失敗時呼叫端顯示 toast，promise 照常 resolve。 */
  onGenerate: (action: string) => Promise<unknown>;
  /** POST 進行中。 */
  generating: boolean;
  /** 本頁角色動作排隊中＋生成中的張數。 */
  activeCount: number;
}

/** 角色模式的面板：動作描述（1–300 字）、十個常用動作、產生角色動作（一次一張，整段停用防連點）。 */
export function PosePanel({ page, hasCharacter, characterHref, onGenerate, generating, activeCount }: PosePanelProps) {
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
      await onGenerate(trimmed);
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
      <Button variant="primary" size="lg" disabled={!hasCharacter || !trimmed || generating || starting} onClick={() => void generate()}>
        產生角色動作
      </Button>
      {hasCharacter ? (
        <span className={styles.hintCenter} role="status">
          {activeCount > 0 ? `本頁 ${activeCount} 張排隊或生成中` : "約 10 秒"}
        </span>
      ) : (
        <span className={styles.hintCenter}>
          <Link href={characterHref}>先設定角色</Link>
        </span>
      )}
    </section>
  );
}
