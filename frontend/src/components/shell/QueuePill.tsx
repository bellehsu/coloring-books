"use client";

import { useEffect, useState } from "react";
import { useQueue } from "@/api/hooks";
import { estimateSeconds, formatRemaining } from "@/lib/eta";
import styles from "./QueuePill.module.css";

/** 佇列清空後，膠囊維持「GPU 就緒」的時間。 */
const READY_MS = 5 * 60_000;

type Track = {
  /** 自佇列上次清空以來看到的工作數（m）。 */
  total: number;
  /** 上一次看到的待處理數。 */
  prev: number;
  /** 佇列剛清空、還在「就緒」期間。 */
  ready: boolean;
};

export function QueuePill() {
  const { data } = useQueue();
  const pending = data?.pending ?? [];
  const count = pending.length;
  const [track, setTrack] = useState<Track>({
    total: 0,
    prev: 0,
    ready: false,
  });

  // 依待處理數的變化調整 track（在 render 中比對上一次的值，不用 effect）。
  if (count !== track.prev) {
    if (count > 0) {
      // 新增的工作（待處理數增加）算進 m；完成的工作不會讓 m 變小。
      const total =
        track.prev === 0
          ? count
          : track.total + Math.max(0, count - track.prev);
      setTrack({ total, prev: count, ready: false });
    } else {
      setTrack({ total: 0, prev: 0, ready: true });
    }
  }

  const ready = track.ready;
  useEffect(() => {
    if (!ready) return;
    const id = setTimeout(
      () => setTrack((t) => ({ ...t, ready: false })),
      READY_MS,
    );
    return () => clearTimeout(id);
  }, [ready]);

  let tone: "ok" | "warn" | "idle" = "idle";
  let text = "GPU 閒置";
  if (data?.state === "engine_unavailable") {
    tone = "warn";
    text = "生成引擎無法連線";
  } else if (data?.state === "stopped") {
    tone = "warn";
    text = "生成服務已停止";
  } else if (count > 0) {
    tone = "ok";
    // n = 正在做的是第幾張（m − 剩餘張數 + 1，剩餘含進行中的這張）
    const m = Math.max(track.total, count);
    const n = m - count + 1;
    text = `生成中 ${n}/${m} · ${formatRemaining(estimateSeconds(pending, data?.recent ?? []))}`;
  } else if (ready) {
    tone = "ok";
    text = "GPU 就緒";
  }
  return (
    <span
      role="status"
      className={styles.pill}
      title={data?.last_error ?? undefined}
    >
      <span className={`${styles.dot} ${styles[tone]}`} aria-hidden="true" />
      {text}
      {data?.last_error && (
        <span className={styles.srOnly}>{`（${data.last_error}）`}</span>
      )}
    </span>
  );
}
