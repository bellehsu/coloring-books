"use client";

import { useEffect, useSyncExternalStore } from "react";
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

const INITIAL: Track = { total: 0, prev: 0, ready: false };

/**
 * n/m 與「就緒」放在模組層級：膠囊在每個頁面的 AppBar 裡，換頁會卸載再掛上，
 * 放在元件 state 的話 n/m 會從 1 重新算起、就緒會提早消失。
 */
const tracker = {
  state: INITIAL,
  listeners: new Set<() => void>(),
  timer: null as ReturnType<typeof setTimeout> | null,
  set(next: Track) {
    this.state = next;
    this.listeners.forEach((fn) => fn());
  },
  /** 依待處理數的變化調整：新增的工作算進 m（完成的不會讓 m 變小）；清空時進入就緒，READY_MS 後結束。 */
  observe(count: number) {
    const t = this.state;
    if (count === t.prev) return;
    if (this.timer != null) clearTimeout(this.timer);
    this.timer = null;
    if (count > 0) {
      const total = t.prev === 0 ? count : t.total + Math.max(0, count - t.prev);
      this.set({ total, prev: count, ready: false });
    } else {
      this.set({ total: 0, prev: 0, ready: true });
      this.timer = setTimeout(() => {
        this.timer = null;
        this.set({ ...this.state, ready: false });
      }, READY_MS);
    }
  },
};
const subscribe = (fn: () => void) => {
  tracker.listeners.add(fn);
  return () => void tracker.listeners.delete(fn);
};
const snapshot = () => tracker.state;

/** 測試用：清掉模組層級的計數與就緒計時。 */
export function resetQueueTracker() {
  if (tracker.timer != null) clearTimeout(tracker.timer);
  tracker.timer = null;
  tracker.state = INITIAL;
}

export function QueuePill() {
  const { data } = useQueue();
  const pending = data?.pending ?? [];
  const count = pending.length;
  const track = useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => {
    if (data) tracker.observe(count);
  }, [data, count]);
  const ready = track.ready;

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
