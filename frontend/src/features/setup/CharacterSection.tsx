"use client";

import { useState, type ChangeEvent } from "react";
import type { Book, Generation } from "@/api/client";
import { useGenerations, useSetupCharacter } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editor/errMsg";
import { UPLOAD_ACCEPT } from "@/lib/upload";
import styles from "./SetupPage.module.css";

const MAX_DESC = 800;
const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

/** 確認「換主角？」之後要做的事。 */
type Pending = { kind: "choose"; generationId: number } | { kind: "upload"; file: File };

export interface CharacterSectionProps {
  /** null：書還沒建立（/books/new）；第一次要用到書時呼叫 ensureBook。 */
  bookId: number | null;
  book: Book | undefined;
  titleValid: boolean;
  /** 建立書（失敗時已提示，這裡不必再顯示）。 */
  ensureBook: () => Promise<number>;
}

/**
 * ② 主角：描述 → 產生（一次一張，「再畫一隻」）→ 選一隻；或「↑ 上傳自己的主角」。
 * 已經有主角時換一隻（選別隻或上傳）先問「換主角？已經畫好的動作會保留」，確認後才帶 confirm_change。
 */
export function CharacterSection({ bookId, book, titleValid, ensureBook }: CharacterSectionProps) {
  const toast = useToast();
  const gensQ = useGenerations(bookId ?? 0, { kind: "character" }, bookId != null);
  const { generate, choose, upload } = useSetupCharacter();
  const [typed, setTyped] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);

  const list = [...(gensQ.data ?? [])]
    .filter((g) => !g.discarded && g.status !== "cancelled")
    .sort((a, c) => a.id - c.id);
  const selectedId = book?.character_generation_id ?? null;
  const hasCharacter = selectedId != null;
  const lastText = [...list].reverse().find((g) => g.user_text)?.user_text;
  const description = typed ?? book?.character_description ?? lastText ?? "";
  const trimmed = description.trim();
  const descValid = trimmed.length >= 1 && trimmed.length <= MAX_DESC;
  const drawing = generate.isPending || list.some(isActive);
  const changing = choose.isPending || upload.isPending;
  const canBook = bookId != null || titleValid;
  const drawLabel = list.length === 0 ? "＋ 產生主角" : "＋ 再畫一隻";

  async function draw() {
    if (!descValid || drawing || changing || !canBook) return;
    let id: number;
    try {
      id = await ensureBook();
    } catch {
      return;
    }
    // 已有主角：這只是多畫一張候選，主角要等選了才換（那時再確認）
    generate.mutate(
      { bookId: id, description: trimmed, confirm: hasCharacter },
      { onError: (e) => toast.show(errMsg(e, "沒辦法畫主角，請再試一次"), "error") },
    );
  }

  function run(p: Pending, id: number, confirm: boolean) {
    const onError = (e: unknown) => toast.show(errMsg(e, "沒辦法換主角，請再試一次"), "error");
    if (p.kind === "choose") choose.mutate({ bookId: id, generationId: p.generationId, confirm }, { onError });
    else upload.mutate({ bookId: id, file: p.file, confirm }, { onError: (e) => toast.show(errMsg(e, "上傳不了這張圖"), "error") });
  }

  function pick(g: Generation) {
    if (bookId == null || g.id === selectedId || changing) return;
    if (hasCharacter) setPending({ kind: "choose", generationId: g.id });
    else run({ kind: "choose", generationId: g.id }, bookId, false);
  }

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同一個檔案再選一次也會觸發
    if (!file || changing) return;
    let id: number;
    try {
      id = await ensureBook();
    } catch {
      return;
    }
    if (hasCharacter) setPending({ kind: "upload", file });
    else run({ kind: "upload", file }, id, false);
  }

  function confirmChange() {
    if (!pending || bookId == null) return;
    run(pending, bookId, true);
    setPending(null);
  }

  return (
    <section aria-labelledby="setup-s2" className={styles.card}>
      <div className={styles.cardHead}>
        <span className={`${styles.num} ${styles.numSky}`} aria-hidden="true">
          2
        </span>
        <h2 id="setup-s2" className={styles.h2}>
          主角
        </h2>
        {hasCharacter && <span className={styles.done}>選好了 ✓</span>}
      </div>
      <label className={styles.field}>
        <span className={styles.label}>主角長什麼樣子？</span>
        <input
          type="text"
          className={styles.input}
          value={description}
          maxLength={MAX_DESC + 200}
          placeholder="例如：一隻圓圓的小白兔，長耳朵，圍著紅色圍巾"
          aria-invalid={trimmed.length > MAX_DESC}
          onChange={(e) => setTyped(e.target.value)}
        />
      </label>
      <p className={styles.hint}>
        中文或英文都可以。衣服避免條紋、格子等細碎圖樣，線稿會比較乾淨。
        {trimmed.length > MAX_DESC && `（超過 ${MAX_DESC} 字）`}
        {!canBook && "先寫書名。"}
      </p>
      <ul className={styles.cands} aria-label="主角候選">
        {list.map((g, i) => (
          <Candidate
            key={g.id}
            g={g}
            n={i + 1}
            on={g.id === selectedId}
            busy={changing}
            onPick={() => pick(g)}
          />
        ))}
        <li className={styles.candSlot}>
          <button
            type="button"
            className={styles.drawCard}
            // 選定／上傳還沒存好時也不能畫：不然送出時還不知道已經有主角（沒帶 confirm_change → 409）
            disabled={!descValid || drawing || changing || !canBook}
            onClick={() => void draw()}
          >
            {drawing ? "正在畫…" : drawLabel}
          </button>
        </li>
        <li className={styles.candSlot}>
          <label className={styles.uploadCard} data-disabled={changing || !canBook}>
            <b>
              <span aria-hidden="true">↑</span> 上傳自己的主角
            </b>
            <span className={styles.uploadHint}>白底的圖最好</span>
            <input
              type="file"
              accept={UPLOAD_ACCEPT}
              aria-label="上傳自己的主角"
              className={styles.file}
              disabled={changing || !canBook}
              onChange={(e) => void onFile(e)}
            />
          </label>
        </li>
      </ul>

      <Dialog open={pending != null} onClose={() => setPending(null)} title="換主角？">
        <div className={styles.confirm}>
          <p>已經畫好的動作會保留，之後新畫的動作會用新的主角。</p>
          <div className={styles.confirmActions}>
            <Button onClick={() => setPending(null)}>先不要</Button>
            <Button variant="primary" onClick={confirmChange}>
              換主角
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}

function Candidate({ g, n, on, busy, onPick }: { g: Generation; n: number; on: boolean; busy: boolean; onPick: () => void }) {
  const ok = g.status === "succeeded" && !!g.image_url;
  const running = g.status === "running" || g.queue_position === 0;
  return (
    <li className={styles.cand} data-on={on ? "true" : "false"}>
      <div className={styles.candArt}>
        {ok ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={g.image_url!} alt={`主角候選 ${n}`} className={styles.candImg} />
        ) : (
          <span className={g.status === "failed" ? styles.candFail : styles.candWait}>
            {g.status === "failed" ? g.error || "沒畫出來" : running ? "正在畫…" : "排隊中"}
          </span>
        )}
      </div>
      {ok && (
        <button type="button" className={styles.pick} aria-pressed={on} disabled={busy && !on} onClick={onPick}>
          {on ? "就是牠 ✓" : "選這隻"}
        </button>
      )}
    </li>
  );
}
