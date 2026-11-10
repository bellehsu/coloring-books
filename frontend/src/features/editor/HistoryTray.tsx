"use client";

import { useState } from "react";
import type { Generation } from "@/api/client";
import { useCancelGeneration, useGenerations, useUpdateGeneration } from "@/api/hooks";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "./errMsg";
import styles from "./HistoryTray.module.css";

/** 有歷史托盤的種類。合成圖（composite）沒有托盤：只能經由「確認合成」產生或選回，不可 /select。 */
export type HistoryKind = "background" | "pose";

const TEXT: Record<HistoryKind, { title: string; noun: string; empty: string }> = {
  background: { title: "背景歷史", noun: "背景", empty: "還沒有背景。寫好場景描述後按「產生背景」。" },
  pose: { title: "角色動作歷史", noun: "角色動作", empty: "還沒有角色動作。輸入動作後按「產生角色動作」。" },
};

export interface HistoryTrayProps {
  bookId: number;
  kind: HistoryKind;
  pageId: number;
  /** 頁面目前選用的這個種類的 generation（background_generation_id／pose_generation_id）。 */
  selectedId: number | null;
  /** 點一張可用的（成功、未捨棄、不是使用中的）；呼叫端負責 useSelectBackground／useSelectPose。 */
  onPick: (g: Generation) => void;
  /** 選用請求進行中：其他張暫時不能點（避免連點）。 */
  picking?: boolean;
}

/**
 * 舞台下方的歷史托盤：這一頁這個種類的所有 generations（新的在前）。
 * 排隊位置／進度／失敗訊息；選用（綠框「使用中」）、捨棄／還原、取消；「歷史 N」切換顯示已捨棄的。
 */
export function HistoryTray({ bookId, kind, pageId, selectedId, onPick, picking }: HistoryTrayProps) {
  const toast = useToast();
  const t = TEXT[kind];
  const q = useGenerations(bookId, { kind, pageId, includeDiscarded: true });
  const updateGen = useUpdateGeneration(bookId);
  const cancel = useCancelGeneration(bookId);
  const [showHistory, setShowHistory] = useState(false);

  const sorted = [...(q.data ?? [])].sort((a, b) => b.id - a.id);
  const discardedN = sorted.filter((g) => g.discarded).length;
  const shown = showHistory ? sorted : sorted.filter((g) => !g.discarded);

  const toggleDiscard = (g: Generation) =>
    updateGen.mutate(
      { id: g.id, discarded: !g.discarded },
      { onError: (e) => toast.show(errMsg(e, "無法更新"), "error") },
    );
  const cancelGen = (g: Generation) =>
    cancel.mutate(g.id, { onError: (e) => toast.show(errMsg(e, "無法取消"), "error") });

  return (
    <section aria-label={t.title} className={styles.tray}>
      <div className={styles.head}>
        <b className={styles.h}>{t.title}</b>
        <span className={styles.sub}>點一張設為本頁的{t.noun}</span>
        <button
          type="button"
          className={styles.history}
          aria-pressed={showHistory}
          title={showHistory ? `隱藏已捨棄的${t.noun}` : `顯示已捨棄的${t.noun}`}
          onClick={() => setShowHistory((v) => !v)}
        >
          <Icons.clock size={16} />
          歷史 {discardedN}
        </button>
      </div>
      {q.isError ? (
        <p role="alert" className={styles.empty}>
          {errMsg(q.error, `無法載入${t.noun}`)}
        </p>
      ) : !q.data ? (
        <p className={styles.empty} aria-busy="true">
          載入中…
        </p>
      ) : shown.length === 0 ? (
        <p className={styles.empty}>{t.empty}</p>
      ) : (
        <ul className={styles.row}>
          {shown.map((g) => (
            <Card
              key={g.id}
              g={g}
              noun={t.noun}
              on={g.id === selectedId}
              disabled={picking}
              onPick={() => onPick(g)}
              onToggle={() => toggleDiscard(g)}
              onCancel={() => cancelGen(g)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Card({
  g,
  noun,
  on,
  disabled,
  onPick,
  onToggle,
  onCancel,
}: {
  g: Generation;
  noun: string;
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
  const aspect = g.width > 0 && g.height > 0 ? `${g.width} / ${g.height}` : undefined;
  const thumbStyle = aspect ? { aspectRatio: aspect } : undefined;

  return (
    <li className={styles.card} data-on={on ? "true" : "false"} data-off={g.discarded ? "true" : "false"} data-wait={pending ? "true" : "false"}>
      {ok && !g.discarded ? (
        <button
          type="button"
          className={styles.pick}
          aria-label={`選 seed ${g.seed} 為本頁${noun}`}
          aria-pressed={on}
          disabled={disabled && !on}
          onClick={on ? undefined : onPick}
        >
          <span className={styles.thumb} style={thumbStyle} data-aspect={aspect}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={g.image_url!} alt="" className={styles.img} loading="lazy" />
          </span>
          {on && <span className={styles.badge}>使用中</span>}
        </button>
      ) : (
        <div className={styles.thumb} style={thumbStyle} data-aspect={aspect}>
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
            title={g.discarded ? "還原這張" : on ? "使用中的不能捨棄" : "捨棄這張"}
            // 後端不讓捨棄頁面正在用的背景／角色動作（409）；先換一張再捨棄
            disabled={on && !g.discarded}
            onClick={onToggle}
          >
            {g.discarded ? <Icons.undo size={16} /> : <Icons.trash size={16} />}
          </button>
        )}
      </div>
    </li>
  );
}
