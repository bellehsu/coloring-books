"use client";

import { useRef, type KeyboardEvent } from "react";
import type { Generation } from "@/api/client";
import { useCancelGeneration, useGenerations, useUpdateGeneration } from "@/api/hooks";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import styles from "./Thumbs.module.css";

/** 有縮圖列的種類。合成圖（composite）沒有：只能經由合成產生，不可 /select。 */
export type ThumbKind = "background" | "pose";

const TEXT: Record<ThumbKind, { noun: string; empty: string }> = {
  background: { noun: "背景", empty: "還沒有背景。按「換一張」產生一張，或上傳圖片。" },
  pose: { noun: "角色動作", empty: "還沒有角色動作。寫好動作後按「換一張」。" },
};

/** 這頁這個種類的 generations（不含已捨棄），舊的在前；縮圖的「第 N 張」就是這個順序。 */
export function useThumbList(bookId: number, kind: ThumbKind, pageId: number) {
  const q = useGenerations(bookId, { kind, pageId });
  const list = q.data ? [...q.data].sort((a, b) => a.id - b.id) : undefined;
  return { q, list };
}

/** 在 useThumbList 的清單裡是第幾張（1 起算）；不在清單裡回 null。 */
export function thumbNumber(list: Generation[] | undefined, id: number | null | undefined): number | null {
  const i = list && id != null ? list.findIndex((g) => g.id === id) : -1;
  return i >= 0 ? i + 1 : null;
}

export interface ThumbsProps {
  bookId: number;
  kind: ThumbKind;
  pageId: number;
  /** 頁面目前選用的這個種類的 generation（background_generation_id／pose_generation_id）。 */
  selectedId: number | null;
  /** 點一張可用的（成功、不是使用中的）；呼叫端負責 useSelectBackground／useSelectPose。 */
  onPick: (g: Generation) => void;
  /** 選用請求進行中：其他張暫時不能點（避免連點）。 */
  picking?: boolean;
}

/**
 * 構圖卡片裡的縮圖列（單選）：這頁這個種類的 generations，舊的在前。
 * 成功的是選項（方向鍵移動焦點、Enter／空白鍵選用；選用中其他張 aria-disabled）；排隊中／生成中顯示進度與取消 ×；
 * 沒在用的（含失敗、已取消）可以捨棄 ×。使用中的不能捨棄（後端 409）。上傳的背景有「上傳」標記。
 */
export function Thumbs({ bookId, kind, pageId, selectedId, onPick, picking }: ThumbsProps) {
  const toast = useToast();
  const t = TEXT[kind];
  const { q, list } = useThumbList(bookId, kind, pageId);
  const updateGen = useUpdateGeneration(bookId);
  const cancel = useCancelGeneration(bookId);
  const radios = useRef<(HTMLButtonElement | null)[]>([]);

  const discard = (g: Generation) =>
    updateGen.mutate({ id: g.id, discarded: true }, { onError: (e) => toast.show(errMsg(e, "無法捨棄"), "error") });
  const cancelGen = (g: Generation) =>
    cancel.mutate(g.id, { onError: (e) => toast.show(errMsg(e, "無法取消"), "error") });

  if (q.isError) {
    return (
      <p role="alert" className={styles.empty}>
        {errMsg(q.error, `無法載入${t.noun}`)}
      </p>
    );
  }
  if (!list) {
    return (
      <p className={styles.empty} aria-busy="true">
        載入中…
      </p>
    );
  }
  if (list.length === 0) return <p className={styles.empty}>{t.empty}</p>;

  const pickable = list.filter((g) => g.status === "succeeded" && !!g.image_url);
  // 單選群組只有一個 Tab 停留點：選用的那張，沒有就第一張
  const tabStop = pickable.some((g) => g.id === selectedId) ? selectedId : (pickable[0]?.id ?? null);

  const onKeyDown = (e: KeyboardEvent, id: number) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const n = pickable.length;
    const i = pickable.findIndex((g) => g.id === id);
    radios.current[(i + step + n) % n]?.focus();
  };

  return (
    <div role="radiogroup" aria-label={`選擇${t.noun}`} className={styles.row}>
      {list.map((g, i) => {
        const label = `${t.noun}第 ${i + 1} 張`;
        const on = g.id === selectedId;
        const upload = g.source === "upload";
        const pending = g.status === "queued" || g.status === "running";
        const ok = g.status === "succeeded" && !!g.image_url;
        const k = pickable.indexOf(g);
        const aspect = g.width > 0 && g.height > 0 ? `${g.width} / ${g.height}` : undefined;
        return (
          <div key={g.id} className={styles.tile} data-tile data-on={on} data-wait={pending}>
            {ok ? (
              <button
                ref={(el) => {
                  radios.current[k] = el;
                }}
                type="button"
                role="radio"
                aria-checked={on}
                aria-label={upload ? `${label}（上傳的圖片）` : label}
                tabIndex={g.id === tabStop ? 0 : -1}
                className={styles.pick}
                // 不用 disabled：用鍵盤選用時焦點會因按鈕停用而掉到 body
                aria-disabled={picking && !on ? true : undefined}
                onClick={on || picking ? undefined : () => onPick(g)}
                onKeyDown={(e) => onKeyDown(e, g.id)}
              >
                <span className={styles.thumb} style={aspect ? { aspectRatio: aspect } : undefined} data-aspect={aspect}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={g.image_url!} alt="" className={styles.img} loading="lazy" />
                </span>
              </button>
            ) : (
              <Placeholder g={g} aspect={aspect} />
            )}
            {upload && (
              <span className={styles.badge} aria-hidden="true">
                上傳
              </span>
            )}
            {pending ? (
              <button type="button" className={styles.x} aria-label={`取消${label}`} title="取消" onClick={() => cancelGen(g)}>
                <Icons.close size={14} />
              </button>
            ) : (
              !on && (
                <button type="button" className={styles.x} aria-label={`捨棄${label}`} title="捨棄這張" onClick={() => discard(g)}>
                  <Icons.close size={14} />
                </button>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 不能選的那幾種：排隊中、生成中（進度）、失敗（原因在 title，螢幕閱讀器另外讀出）、已取消、成功但沒有圖。 */
function Placeholder({ g, aspect }: { g: Generation; aspect: string | undefined }) {
  const running = g.status === "running" || g.queue_position === 0;
  const pct = g.progress && g.progress[1] > 0 ? Math.min(100, (g.progress[0] / g.progress[1]) * 100) : 0;
  let body;
  if (g.status === "queued" || g.status === "running") {
    body = (
      <>
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
      </>
    );
  } else if (g.status === "failed") {
    body = (
      <span className={styles.fail} title={g.error || "生成失敗"}>
        失敗<span className={styles.srOnly}>：{g.error || "生成失敗"}</span>
      </span>
    );
  } else if (g.status === "cancelled") {
    body = <span>已取消</span>;
  } else {
    body = <span>沒有圖片</span>;
  }
  return (
    <div className={`${styles.thumb} ${styles.wait}`} style={aspect ? { aspectRatio: aspect } : undefined} data-aspect={aspect}>
      {body}
    </div>
  );
}
