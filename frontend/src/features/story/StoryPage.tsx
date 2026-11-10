"use client";

import { useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, type Generation, type Page } from "@/api/client";
import {
  useBatchMaterials,
  useBook,
  useCreatePage,
  useGenerations,
  usePages,
  useUpdatePage,
  useUploadBackground,
} from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { BookBar } from "@/components/shell/BookBar";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { ACTION_HINT } from "@/lib/actions";
import { ImportDialog } from "./ImportDialog";
import { slotView, StoryRow, type BgMode } from "./StoryRow";
import styles from "./StoryPage.module.css";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const isActive = (g: Generation) => g.status === "queued" || g.status === "running";
const MAX_SKIP_NAMES = 4;

/** 這一頁（這種素材）有沒有已選、已成功或排隊中的紀錄：有就不用再排。 */
function hasMaterial(slotId: number | null, gens: readonly Generation[]) {
  return slotId != null || gens.some((g) => !g.discarded && (g.status === "succeeded" || isActive(g)));
}

export function StoryPage({ bookId }: { bookId: number }) {
  const router = useRouter();
  const toast = useToast();
  const book = useBook(bookId);
  const pagesQ = usePages(bookId);
  const gensQ = useGenerations(bookId);
  const createPage = useCreatePage(bookId);
  const updatePage = useUpdatePage(bookId);
  const upload = useUploadBackground(bookId);
  const batch = useBatchMaterials(bookId);
  const actionHintId = useId();

  const [modes, setModes] = useState<Record<number, BgMode>>({});
  const [files, setFiles] = useState<Record<number, string>>({});
  const [importOpen, setImportOpen] = useState(false);
  const pendingSaves = useRef(new Set<Promise<unknown>>());

  const pages = useMemo(
    () => [...(pagesQ.data ?? [])].sort((a, b) => a.position - b.position || a.id - b.id),
    [pagesQ.data],
  );
  const byPage = useMemo(() => {
    const m = new Map<number, { background: Generation[]; pose: Generation[] }>();
    for (const g of gensQ.data ?? []) {
      if (g.page_id == null || (g.kind !== "background" && g.kind !== "pose")) continue;
      const e = m.get(g.page_id) ?? { background: [], pose: [] };
      e[g.kind].push(g);
      m.set(g.page_id, e);
    }
    return m;
  }, [gensQ.data]);
  const gensOf = (p: Page) => byPage.get(p.id) ?? { background: [], pose: [] };

  const modeOf = (p: Page): BgMode => {
    const set = modes[p.id];
    if (set) return set;
    const sel = gensOf(p).background.find((g) => g.id === p.background_generation_id);
    return sel?.source === "upload" ? "upload" : "ai";
  };

  const missing = pages.reduce((k, p) => {
    const g = gensOf(p);
    const bg = modeOf(p) === "ai" && p.scene_prompt.trim() !== "" && !hasMaterial(p.background_generation_id, g.background);
    const pose = p.action_prompt.trim() !== "" && !hasMaterial(p.pose_generation_id, g.pose);
    return k + +bg + +pose;
  }, 0);
  const busy = (gensQ.data ?? []).some((g) => (g.kind === "background" || g.kind === "pose") && isActive(g));

  const save = (pageId: number, patch: { scene_prompt?: string; action_prompt?: string }) => {
    const p = updatePage.mutateAsync({ pageId, ...patch }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存，請再試一次"), "error");
      throw e;
    });
    pendingSaves.current.add(p);
    const done = () => pendingSaves.current.delete(p);
    p.then(done, done);
    return p;
  };

  const doUpload = (p: Page, file: File) =>
    upload.mutateAsync({ pageId: p.id, file }).then(
      () => setFiles((f) => ({ ...f, [p.id]: file.name })),
      (e: unknown) => toast.show(errMsg(e, "無法上傳圖片"), "error"),
    );

  async function generateAll() {
    if (batch.isPending) return;
    // 剛輸入完的文字先寫完，後端才會看到
    await Promise.allSettled([...pendingSaves.current]);
    const uploadIds = pages.filter((p) => modeOf(p) === "upload").map((p) => p.id);
    try {
      const res = await batch.mutateAsync({ upload_page_ids: uploadIds });
      const nameOf = (id: number) => `第 ${pages.findIndex((p) => p.id === id) + 1} 頁`;
      let text = `已排入 ${res.queued.length} 張`;
      if (res.skipped.length > 0) {
        const names = res.skipped.slice(0, MAX_SKIP_NAMES).map((s) => `${nameOf(s.page_id)} ${s.reason}`);
        text += `；略過 ${res.skipped.length} 項：${names.join("、")}${res.skipped.length > MAX_SKIP_NAMES ? " 等" : ""}`;
      }
      toast.show(text, res.queued.length > 0 ? "success" : "info");
    } catch (e) {
      toast.show(errMsg(e, "無法產生素材"), "error");
    }
  }

  const b = book.data;
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

  return (
    <>
      <AppBar crumbs={[{ label: "我的繪本", href: "/" }, { label: b.title }]} />
      <BookBar book={b} current="story" onExport={() => router.push(`/books/${bookId}/editor`)} />
      <main className={styles.main}>
        <div className={styles.head}>
          <div className={styles.titles}>
            <h2 className={styles.h2}>寫下每一頁的故事</h2>
            <span className={styles.hint}>
              每頁兩句中文就夠了。寫完按一次「產生全部素材」，背景和角色動作會一起排隊生成。
            </span>
            <span id={actionHintId} className={styles.hint}>
              {ACTION_HINT}
            </span>
          </div>
          <div className={styles.actions}>
            <Button disabled={!pagesQ.isSuccess} onClick={() => setImportOpen(true)}>
              貼上整本故事
            </Button>
            <Button disabled={createPage.isPending} onClick={() => createPage.mutate({ scene_prompt: "", action_prompt: "" }, { onError: (e) => toast.show(errMsg(e, "無法新增頁面"), "error") })}>
              ＋ 新增一頁
            </Button>
            <Button variant="primary" disabled={missing === 0 || batch.isPending} onClick={() => void generateAll()}>
              產生全部素材（還有 {missing} 張）
            </Button>
          </div>
        </div>
        {busy && (
          <div role="status" className={styles.banner}>
            正在產生素材，可以先去「頁面」擺已經好的頁面，不用等全部完成。
          </div>
        )}
        {pagesQ.isError && (
          <p role="alert" className={styles.loadError}>
            {errMsg(pagesQ.error, "無法載入頁面")}
          </p>
        )}
        {pagesQ.isSuccess && pages.length === 0 && (
          <p className={styles.hint}>還沒有頁面。按「＋ 新增一頁」，或「貼上整本故事」一次建好。</p>
        )}
        {pages.map((p, i) => (
          <StoryRow
            key={p.id}
            n={i + 1}
            page={p}
            mode={modeOf(p)}
            onModeChange={(m) => setModes((s) => ({ ...s, [p.id]: m }))}
            fileName={files[p.id]}
            uploading={upload.isPending && upload.variables?.pageId === p.id}
            onUpload={(f) => void doUpload(p, f)}
            onSave={(patch) => save(p.id, patch)}
            bg={slotView(gensOf(p).background, p.background_generation_id)}
            pose={slotView(gensOf(p).pose, p.pose_generation_id)}
            onRetry={() => void generateAll()}
            actionHintId={actionHintId}
          />
        ))}
      </main>
      {pagesQ.isSuccess && <ImportDialog bookId={bookId} open={importOpen} onClose={() => setImportOpen(false)} />}
    </>
  );
}
