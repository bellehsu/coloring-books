"use client";

import { useState } from "react";
import type { Generation } from "@/api/client";
import { Icons } from "@/components/ui/Icons";
import styles from "./CandidateTray.module.css";

export interface CandidateTrayProps {
  /** 本頁所有 generations（含已捨棄）；順序不拘，元件內新的在前。 */
  generations: Generation[];
  selectedId: number | null;
  onSelect: (g: Generation) => void;
  onToggleDiscard: (g: Generation) => void;
  onCancel: (g: Generation) => void;
  /** 選圖請求進行中（避免連點）。 */
  selecting?: boolean;
}

export function CandidateTray({ generations, selectedId, onSelect, onToggleDiscard, onCancel, selecting }: CandidateTrayProps) {
  const [showHistory, setShowHistory] = useState(false);
  const sorted = [...generations].sort((a, b) => b.id - a.id);
  const discardedN = sorted.filter((g) => g.discarded).length;
  const shown = showHistory ? sorted : sorted.filter((g) => !g.discarded);

  return (
    <section aria-label="候選圖" className={styles.tray}>
      <div className={styles.head}>
        <b className={styles.h}>候選圖</b>
        <span className={styles.sub}>點一張設為本頁的圖</span>
        <button
          type="button"
          className={styles.history}
          aria-pressed={showHistory}
          title={showHistory ? "隱藏已捨棄的候選圖" : "顯示已捨棄的候選圖"}
          onClick={() => setShowHistory((v) => !v)}
        >
          <Icons.clock size={16} />
          歷史 {discardedN}
        </button>
      </div>
      {shown.length === 0 ? (
        <p className={styles.empty}>還沒有候選圖。寫好場景描述後按「產生 4 張候選」。</p>
      ) : (
        <ul className={styles.row}>
          {shown.map((g) => (
            <Card
              key={g.id}
              g={g}
              on={g.id === selectedId}
              disabled={selecting}
              onPick={() => onSelect(g)}
              onToggle={() => onToggleDiscard(g)}
              onCancel={() => onCancel(g)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Card({
  g,
  on,
  disabled,
  onPick,
  onToggle,
  onCancel,
}: {
  g: Generation;
  on: boolean;
  disabled?: boolean;
  onPick: () => void;
  onToggle: () => void;
  onCancel: () => void;
}) {
  const ok = g.status === "succeeded" && !!g.image_url;
  const pending = g.status === "queued" || g.status === "running";
  const running = g.status === "running" || g.queue_position === 0;
  const pct = g.progress && g.progress[1] > 0 ? Math.min(100, (g.progress[0] / g.progress[1]) * 100) : 0;

  return (
    <li className={styles.card} data-on={on ? "true" : "false"} data-off={g.discarded ? "true" : "false"} data-wait={pending ? "true" : "false"}>
      {ok && !g.discarded ? (
        <button
          type="button"
          className={styles.pick}
          aria-label={`選 seed ${g.seed} 為本頁圖片`}
          aria-pressed={on}
          disabled={disabled && !on}
          onClick={on ? undefined : onPick}
        >
          <span className={styles.thumb}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={g.image_url!} alt="" className={styles.img} loading="lazy" />
          </span>
          {on && <span className={styles.badge}>使用中</span>}
        </button>
      ) : (
        <div className={styles.thumb}>
          {ok && (
            // 已捨棄：只看不選（先還原）
            // eslint-disable-next-line @next/next/no-img-element
            <img src={g.image_url!} alt={`已捨棄 seed ${g.seed}`} className={styles.img} loading="lazy" />
          )}
          {pending && (
            <div className={styles.wait}>
              <span>{running ? "生成中" : `排隊第 ${g.queue_position ?? "?"} 位`}</span>
              {running ? (
                <div
                  className={styles.bar}
                  role="progressbar"
                  aria-label="生成進度"
                  aria-valuemin={0}
                  aria-valuemax={g.progress?.[1] ?? 1}
                  aria-valuenow={g.progress?.[0] ?? 0}
                >
                  <div className={styles.fill} style={{ width: `${pct}%` }} />
                </div>
              ) : (
                <div className={styles.bar} aria-hidden="true" />
              )}
            </div>
          )}
          {g.status === "failed" && <div className={`${styles.wait} ${styles.fail}`}>{g.error || "生成失敗"}</div>}
          {g.status === "cancelled" && <div className={styles.wait}>已取消</div>}
        </div>
      )}
      <div className={styles.foot}>
        <span className={styles.seed}>seed {g.seed}</span>
        {pending ? (
          <button type="button" className={styles.iconBtn} aria-label={`取消 seed ${g.seed}`} title="取消" onClick={onCancel}>
            <Icons.close size={16} />
          </button>
        ) : (
          <button
            type="button"
            className={styles.iconBtn}
            aria-label={g.discarded ? `還原 seed ${g.seed}` : `捨棄 seed ${g.seed}`}
            title={g.discarded ? "還原這張" : "捨棄這張"}
            onClick={onToggle}
          >
            {g.discarded ? <Icons.undo size={16} /> : <Icons.trash size={16} />}
          </button>
        )}
      </div>
    </li>
  );
}
