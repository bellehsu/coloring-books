"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, type Generation } from "@/api/client";
import {
  useBook,
  useCancelGeneration,
  useCharacterCandidates,
  useGenerations,
  useSetCharacter,
  useUpdateGeneration,
} from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { BookBar } from "@/components/shell/BookBar";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import { Segmented } from "@/components/ui/Segmented";
import { useToast } from "@/components/ui/Toast";
import styles from "./CharacterStudio.module.css";

const MAX_DESC = 800;
const ArrowRight = Icons["arrow-right"];

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function CharacterStudio({ bookId }: { bookId: number }) {
  const router = useRouter();
  const toast = useToast();
  const book = useBook(bookId);
  const gens = useGenerations(bookId, { kind: "character", includeDiscarded: true });
  const create = useCharacterCandidates(bookId);
  const setCharacter = useSetCharacter(bookId);
  const update = useUpdateGeneration(bookId);
  const cancel = useCancelGeneration(bookId);

  const [typed, setTyped] = useState<string | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [view, setView] = useState<"color" | "line">("color");

  const list = gens.data ?? [];
  const sorted = [...list].sort((a, b) => b.id - a.id);
  const discardedN = list.filter((g) => g.discarded).length;
  const shown = (showHistory ? sorted : sorted.filter((g) => !g.discarded)).slice().sort((a, b) => a.id - b.id);

  const b = book.data;
  const locked = b?.character_generation_id != null;
  const lockedGen = locked ? list.find((g) => g.id === b!.character_generation_id) : undefined;

  const usable = (g: Generation) => g.status === "succeeded" && !g.discarded && !!g.image_url;
  const pickedGen = list.find((g) => g.id === picked && usable(g));
  const selected = locked ? lockedGen : (pickedGen ?? sorted.slice().reverse().find(usable));

  const description = typed ?? (sorted.find((g) => g.status !== "cancelled")?.user_text ?? "");
  const trimmed = description.trim();
  const valid = trimmed.length >= 1 && trimmed.length <= MAX_DESC;

  const queued = list.filter((g) => g.status === "queued" || g.status === "running");
  const running = queued.find((g) => g.queue_position === 0 || g.status === "running");
  const statusText =
    queued.length > 0
      ? `${running ? "正在產生 1 張，" : ""}${running ? queued.length - 1 : queued.length} 張排隊中`
      : "點一張看預覽；不要的可以捨棄";

  function generate() {
    if (!valid || create.isPending) return;
    create.mutate(
      { description: trimmed, count: 4 },
      { onError: (e) => toast.show(errMsg(e, "無法產生候選圖"), "error") },
    );
  }

  function confirm() {
    if (!selected || setCharacter.isPending) return;
    setCharacter.mutate(selected.id, {
      onSuccess: () => router.push(`/books/${bookId}/story`),
      onError: (e) => toast.show(errMsg(e, "無法設定角色"), "error"),
    });
  }

  const toggle = (g: Generation) =>
    update.mutate(
      { id: g.id, discarded: !g.discarded },
      { onError: (e) => toast.show(errMsg(e, "無法更新"), "error") },
    );

  const doCancel = (g: Generation) =>
    cancel.mutate(g.id, { onError: (e) => toast.show(errMsg(e, "無法取消"), "error") });

  if (book.isPending || !b) {
    return (
      <>
        <AppBar crumbs={[{ label: "我的繪本", href: "/" }]} />
        {book.isError ? (
          <p role="alert" className={styles.loadError}>
            {errMsg(book.error, "無法載入繪本")}
          </p>
        ) : (
          <p className={styles.loadError} aria-busy="true">
            載入中…
          </p>
        )}
      </>
    );
  }

  const exportClick = () =>
    locked ? router.push(`/books/${bookId}/editor`) : toast.show("請先設定角色", "info");

  return (
    <>
      <AppBar crumbs={[{ label: "我的繪本", href: "/" }, { label: b.title }]} />
      <BookBar book={b} current="character" onExport={exportClick} />
      <div className={styles.cs}>
        <section aria-label={locked ? "角色" : "產生角色"} className={styles.work}>
          {locked ? (
            <div className={styles.composer}>
              <h2 className={styles.label}>本書角色</h2>
              <p className={styles.desc}>{b.character_description}</p>
              <span className={styles.hint}>角色已設定，不能更換。要換角色請建一本新書。</span>
            </div>
          ) : (
            <div className={styles.composer}>
              <label htmlFor="char-desc" className={styles.label}>
                描述角色的外型
              </label>
              <textarea
                id="char-desc"
                rows={3}
                className={styles.textarea}
                value={description}
                maxLength={MAX_DESC + 200}
                onChange={(e) => setTyped(e.target.value)}
                aria-invalid={trimmed.length > MAX_DESC}
              />
              <div className={styles.composerRow}>
                <span className={styles.hint}>
                  用英文描述效果較好。衣服避免條紋、格子等細碎圖樣，線稿會比較乾淨。
                  {trimmed.length > MAX_DESC && `（超過 ${MAX_DESC} 字）`}
                </span>
                <Button variant="primary" disabled={!valid || create.isPending} onClick={generate}>
                  產生 4 張
                </Button>
              </div>
            </div>
          )}

          <div className={styles.stage}>
            {!locked && (
              <>
                <div className={styles.stageHead}>
                  <b>候選</b>
                  <span className={styles.status}>{statusText}</span>
                  <button
                    type="button"
                    className={styles.history}
                    aria-pressed={showHistory}
                    onClick={() => setShowHistory((v) => !v)}
                  >
                    <Icons.clock size={16} />
                    歷史 {discardedN}
                  </button>
                </div>
                {shown.length === 0 && gens.isSuccess && (
                  <p className={styles.empty}>還沒有候選圖。輸入描述後按「產生 4 張」。</p>
                )}
                <div className={styles.grid}>
                  {shown.map((g) => (
                    <Card
                      key={g.id}
                      g={g}
                      on={selected?.id === g.id}
                      onPick={() => setPicked(g.id)}
                      onToggle={() => toggle(g)}
                      onCancel={() => doCancel(g)}
                    />
                  ))}
                </div>
              </>
            )}
            {locked && (
              <div className={styles.lockedStage}>
                {lockedGen?.image_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={lockedGen.image_url} alt="已設定的角色" className={styles.lockedImg} />
                )}
              </div>
            )}
          </div>
        </section>

        <aside aria-label="選定的角色" className={styles.insp}>
          <div className={styles.inspHead}>
            <h2 className={styles.h2}>{locked ? "本書角色" : "選定的角色"}</h2>
            {selected && <span className={styles.seed}>seed {selected.seed}</span>}
          </div>
          {selected?.image_url ? (
            <>
              <Segmented
                label="預覽"
                value={view}
                onChange={setView}
                options={[
                  { value: "color", label: "彩色" },
                  { value: "line", label: "線稿預覽" },
                ]}
              />
              <div className={styles.previewStage}>
                <div className={styles.paper}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    key={`${selected.id}-${view}`}
                    src={view === "color" ? selected.image_url : `/api/generations/${selected.id}/lineart-preview`}
                    alt={view === "color" ? "角色彩色圖" : "角色線稿預覽"}
                    className={styles.img}
                  />
                </div>
              </div>
            </>
          ) : (
            <p className={styles.empty}>{locked ? "找不到角色圖片。" : "產生並點選一張候選圖，這裡會顯示預覽。"}</p>
          )}

          {locked ? (
            <Link href={`/books/${bookId}/story`} className={styles.go}>
              用這個角色，開始寫故事 →
            </Link>
          ) : (
            <>
              <ul className={styles.checks}>
                {["全身、正面、背景乾淨", "線稿沒有大片黑塊"].map((t) => (
                  <li key={t}>
                    <span className={styles.tick}>
                      <Icons.check size={12} />
                    </span>
                    {t}
                  </li>
                ))}
              </ul>
              <p className={styles.warn}>
                設為本書角色後，每一頁都會參考這張圖，之後不能更換。要換角色請建一本新書。
              </p>
              <Button
                variant="primary"
                size="lg"
                icon={<ArrowRight size={18} />}
                disabled={!selected || setCharacter.isPending}
                onClick={confirm}
                className={styles.confirm}
              >
                設為本書角色，開始寫故事
              </Button>
            </>
          )}
        </aside>
      </div>
    </>
  );
}

function Card({
  g,
  on,
  onPick,
  onToggle,
  onCancel,
}: {
  g: Generation;
  on: boolean;
  onPick: () => void;
  onToggle: () => void;
  onCancel: () => void;
}) {
  const ok = g.status === "succeeded" && !!g.image_url;
  const pending = g.status === "queued" || g.status === "running";
  const running = g.status === "running" || g.queue_position === 0;
  const pct = g.progress && g.progress[1] > 0 ? Math.min(100, (g.progress[0] / g.progress[1]) * 100) : 0;
  return (
    <div className={styles.card} data-on={on ? "true" : "false"} data-off={g.discarded ? "true" : "false"}>
      {ok ? (
        <button type="button" className={styles.pick} aria-label={`選 seed ${g.seed}`} aria-pressed={on} onClick={onPick}>
          <div className={styles.thumb}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={g.image_url!} alt="" className={styles.img} />
          </div>
          {on && (
            <span className={styles.badge}>
              <Icons.check size={14} />
            </span>
          )}
        </button>
      ) : (
        <div className={styles.thumb}>
          {pending && (
            <div className={styles.wait}>
              <span>{running ? "生成中" : `排隊第 ${g.queue_position ?? "?"} 位`}</span>
              {running && g.progress && (
                <div
                  className={styles.bar}
                  role="progressbar"
                  aria-label="生成進度"
                  aria-valuemin={0}
                  aria-valuemax={g.progress[1]}
                  aria-valuenow={g.progress[0]}
                >
                  <div className={styles.fill} style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          )}
          {g.status === "failed" && <div className={`${styles.wait} ${styles.fail}`}>{g.error || "生成失敗"}</div>}
          {g.status === "cancelled" && <div className={styles.wait}>已取消</div>}
        </div>
      )}
      <div className={styles.cardFoot}>
        <span className={styles.seedSm}>seed {g.seed}</span>
        {pending ? (
          <button type="button" className={styles.cancel} aria-label={`取消 seed ${g.seed}`} onClick={onCancel}>
            取消
          </button>
        ) : (
          <button
            type="button"
            className={styles.trash}
            aria-label={g.discarded ? `還原 seed ${g.seed}` : `捨棄 seed ${g.seed}`}
            title={g.discarded ? "還原這張" : "捨棄這張"}
            onClick={onToggle}
          >
            {g.discarded ? <Icons.undo size={16} /> : <Icons.trash size={16} />}
          </button>
        )}
      </div>
    </div>
  );
}
