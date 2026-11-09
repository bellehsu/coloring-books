"use client";

import { useQueue } from "@/api/hooks";
import styles from "./QueuePill.module.css";

export function QueuePill() {
  const { data } = useQueue();
  let tone: "ok" | "warn" | "idle" = "idle";
  let text = "佇列空閒";
  if (data?.state === "engine_unavailable") {
    tone = "warn";
    text = "生成引擎無法連線";
  } else if (data) {
    const n = data.queued + (data.running_id != null ? 1 : 0);
    if (n > 0) {
      tone = "ok";
      text = `生成佇列 ${n} 張`;
    }
  }
  return (
    <span role="status" className={styles.pill} title={data?.last_error ?? undefined}>
      <span className={`${styles.dot} ${styles[tone]}`} aria-hidden="true" />
      {text}
    </span>
  );
}
