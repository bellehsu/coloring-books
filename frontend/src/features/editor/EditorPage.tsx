"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import type { Generation, Page } from "@/api/client";
import {
  useBook,
  useCancelGeneration,
  useCreatePage,
  useDeletePage,
  useGenerations,
  usePageCandidates,
  usePages,
  useQA,
  useReorderPages,
  useSelectGeneration,
  useUpdateGeneration,
  useUpdatePage,
} from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { BookBar } from "@/components/shell/BookBar";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { checklist } from "@/lib/checklist";
import { guidesFromLayout } from "@/lib/raster";
import { CandidateTray } from "./CandidateTray";
import { forgetEditsQueues, peekEditsQueue, pruneEditsQueues } from "./editsQueue";
import { errMsg } from "./errMsg";
import { BookSettingsDialog } from "@/features/books/BookSettingsDialog";
import { ExportDrawer } from "./ExportDrawer";
import { Filmstrip } from "./Filmstrip";
import { GeneratePanel } from "./GeneratePanel";
import { Inspector } from "./Inspector";
import { LineFixStage } from "./LineFixStage";
import { LinePanel } from "./LinePanel";
import { QACompare } from "./QACompare";
import { QAPanel } from "./QAPanel";
import { uploadReference } from "./referenceSketch";
import { SketchCanvas } from "./SketchCanvas";
import { sketchSaverFor } from "./sketchSaver";
import { StageHeader, tabIdOf, type EditorMode } from "./StageHeader";
import { ZoomControls } from "./LineFixCanvas";
import styles from "./EditorPage.module.css";

const isActive = (g: Generation) => g.status === "queued" || g.status === "running";

interface Nav {
  page: number | null;
  mode: EditorMode;
}

function parseNav(sp: URLSearchParams): Nav {
  const n = Number(sp.get("page"));
  const m = sp.get("mode");
  return {
    page: Number.isInteger(n) && n > 0 ? n : null,
    mode: m === "line" || m === "qa" ? m : "sketch",
  };
}

type PagePatch = Partial<Pick<Page, "scene_prompt" | "controlnet_strength" | "line_detail" | "line_width_px">>;

/**
 * 頁面編輯器（/books/{id}/editor?page=<id>&mode=<sketch|line|qa>）。
 * 版面：BookBar＋三區 grid（> 1180 px 三欄；761–1180 兩欄、檢查面板在下；≤ 760 單欄、頁面清單橫向捲動）。
 */
export function EditorPage({ bookId }: { bookId: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const toast = useToast();
  const idPrefix = useId();
  const panelId = `${idPrefix}-stage`;

  const book = useBook(bookId);
  const pagesQ = usePages(bookId);
  const bookGens = useGenerations(bookId, { kind: "page" });

  // ---------- 目前頁與模式：本機狀態為準（立即反應），寫回網址；網址被外部改變時跟著改 ----------
  // 我們寫過、網址還沒追上的 query string（依序）。router.replace 可能晚到：較早的那幾個到了要忽略，
  // 否則連點 A、B 時晚到的 A 會把畫面切回 A。不在清單裡的變化才是外部導覽。
  const qs = sp.toString();
  const [nav, setNav] = useState<Nav>(() => parseNav(sp));
  const [seenQs, setSeenQs] = useState(qs);
  const [writes, setWrites] = useState<string[]>([]);
  if (qs !== seenQs) {
    setSeenQs(qs);
    const i = writes.indexOf(qs);
    if (i === -1) {
      setWrites([]);
      setNav(parseNav(sp));
    } else {
      setWrites(writes.slice(i + 1));
    }
  }

  const pages = pagesQ.data ?? [];
  const current = pages.find((p) => p.id === nav.page) ?? pages[0] ?? null;
  const hasSel = current?.selected_generation_id != null;
  const mode: EditorMode = hasSel ? nav.mode : "sketch";

  const go = (page: number, m: EditorMode) => {
    setNav({ page, mode: m });
    const q = new URLSearchParams();
    q.set("page", String(page));
    if (m !== "sketch") q.set("mode", m);
    const next = q.toString();
    if (next !== qs) setWrites((w) => [...w, next]);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  /** 換頁：目標頁沒選圖就回到構圖模式（否則選圖後會突然跳到修線）。 */
  const selectPage = (id: number) => {
    const target = pages.find((p) => p.id === id);
    go(id, target?.selected_generation_id != null ? nav.mode : "sketch");
  };

  const pageGens = useGenerations(bookId, { kind: "page", pageId: current?.id, includeDiscarded: true });
  const qaQ = useQA(current?.id ?? 0, current != null && hasSel);
  const qa = hasSel ? qaQ.data : undefined;

  const createPage = useCreatePage(bookId);
  const updatePage = useUpdatePage(bookId);
  const deletePage = useDeletePage(bookId);
  const reorder = useReorderPages(bookId);
  const candidates = usePageCandidates(bookId);
  const select = useSelectGeneration(bookId);
  const updateGen = useUpdateGeneration(bookId);
  const cancel = useCancelGeneration(bookId);

  // ---------- 草圖存檔：每頁依呼叫順序（只保留最新），SketchCanvas 卸載後才呼叫也照樣送出 ----------
  const qc = useQueryClient();
  const sketchSaver = sketchSaverFor(qc);
  useEffect(() => {
    sketchSaver.setErrorHandler((e) => toast.show(errMsg(e, "草圖儲存失敗"), "error"));
  }, [sketchSaver, toast]);

  // 換選圖後，舊選圖閒置的修線佇列就不需要了
  useEffect(() => {
    for (const p of pagesQ.data ?? []) pruneEditsQueues(qc, p.id, p.selected_generation_id);
  }, [qc, pagesQ.data]);

  const [zoom, setZoom] = useState(1);
  /** 選圖前正在等修線寫入（候選圖暫時不能點）。 */
  const [flushingSelect, setFlushingSelect] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // ---------- 載入／錯誤 ----------
  const b = book.data;
  if (!b || !pagesQ.data) {
    const err = book.error ?? pagesQ.error;
    return (
      <>
        <AppBar crumbs={[{ label: "我的繪本", href: "/" }]} />
        {err ? (
          <div className={styles.loadError}>
            <p role="alert">{errMsg(err, "無法載入繪本")}</p>
            <Link href="/">回到我的繪本</Link>
          </div>
        ) : (
          <p className={styles.loadError} aria-busy="true">
            載入中…
          </p>
        )}
      </>
    );
  }

  const hasCharacter = b.character_generation_id != null;
  const characterHref = `/books/${bookId}/character`;
  const busyPageIds = new Set(
    (bookGens.data ?? []).filter(isActive).map((g) => g.page_id).filter((x): x is number => x != null),
  );

  // ---------- 動作 ----------
  const addPage = () =>
    createPage.mutate(
      { scene_prompt: "" },
      {
        onSuccess: (p) => go(p.id, "sketch"),
        onError: (e) => toast.show(errMsg(e, "無法新增頁面"), "error"),
      },
    );

  const movePage = (id: number, dir: -1 | 1) => {
    const ids = pages.map((p) => p.id);
    const i = ids.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    reorder.mutate(ids, { onError: (e) => toast.show(errMsg(e, "無法調整順序"), "error") });
  };

  const removePage = (id: number) => {
    const i = pages.findIndex((p) => p.id === id);
    const neighbor = pages[i + 1] ?? pages[i - 1];
    // 先切到相鄰頁（樂觀），避免刪除完成前後閃到別頁；失敗就留在相鄰頁並提示
    if (id === current?.id && neighbor) selectPage(neighbor.id);
    deletePage.mutate(id, {
      onSuccess: () => {
        sketchSaver.forget(id);
        forgetEditsQueues(qc, id);
      },
      onError: (e) => toast.show(errMsg(e, "無法刪除頁面"), "error"),
    });
  };

  const patchPage = (pageId: number, body: PagePatch) =>
    updatePage.mutateAsync({ pageId, ...body }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });

  const generate = (pageId: number) =>
    candidates
      .mutateAsync({ pageId, count: 4 })
      .catch((e: unknown) => toast.show(errMsg(e, "無法產生候選圖"), "error"));

  const pickGen = async (g: Generation) => {
    // 等修線寫入期間再點（連點）一律忽略，避免送出兩次 select
    if (!current || flushingSelect || select.isPending) return;
    const pageId = current.id;
    // 先讓舊選圖的修線寫完：選圖後才到的 PUT 會被後端以「選圖已更換」拒絕，出現多餘的「修線尚未儲存」
    const sel = current.selected_generation_id;
    const pendingEdits = sel != null ? peekEditsQueue(qc, pageId, sel) : undefined;
    if (pendingEdits?.busy()) {
      setFlushingSelect(true);
      try {
        await pendingEdits.flush();
      } catch {
        /* 失敗已提示過 */
      } finally {
        setFlushingSelect(false);
      }
    }
    select.mutate(
      { pageId, generationId: g.id },
      { onError: (e) => toast.show(errMsg(e, "無法選擇這張圖"), "error") },
    );
  };

  const toggleDiscard = (g: Generation) =>
    updateGen.mutate(
      { id: g.id, discarded: !g.discarded },
      { onError: (e) => toast.show(errMsg(e, "無法更新"), "error") },
    );

  const cancelGen = (g: Generation) =>
    cancel.mutate(g.id, { onError: (e) => toast.show(errMsg(e, "無法取消"), "error") });

  const shell = (body: ReactNode) => (
    <>
      <AppBar crumbs={[{ label: "我的繪本", href: "/" }, { label: b.title }]} />
      <BookBar book={b} current="pages" onExport={() => setExportOpen(true)} onSettings={() => setSettingsOpen(true)} />
      {!hasCharacter && (
        <p className={styles.notice}>
          這本繪本還沒設定角色，不能產生頁面圖片。<Link href={characterHref}>前往角色工作室</Link>
        </p>
      )}
      {body}
      <ExportDrawer open={exportOpen} onClose={() => setExportOpen(false)} book={b} />
      <BookSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} book={b} />
    </>
  );

  if (!current) {
    return shell(
      <div className={styles.empty}>
        <h2 className={styles.emptyTitle}>這本繪本還沒有頁面</h2>
        <p className={styles.emptyText}>新增一頁，畫構圖或直接寫場景描述，再產生候選圖。</p>
        <Button variant="primary" size="lg" icon={<Icons.plus size={18} />} disabled={createPage.isPending} onClick={addPage}>
          新增頁面
        </Button>
      </div>,
    );
  }

  const index = pages.indexOf(current);
  const gens = pageGens.data ?? [];
  const selectedGen = gens.find((g) => g.id === current.selected_generation_id);
  const activeCount = gens.filter(isActive).length;
  const items = checklist(current, qa, { seed: selectedGen?.seed });
  const [gw, gh] = b.layout.generation_size;
  const contentPx = b.layout.content_px;

  let stage: ReactNode;
  if (mode === "sketch") {
    const pageId = current.id;
    // 這個 session 存過（可能還在送、或失敗）→ 用最後存的內容；null＝空白。沒存過才用伺服器的 sketch_url。
    const pending = sketchSaver.latestUrl(pageId);
    const sketchSeed = pending !== undefined ? pending : current.sketch_url;
    stage = (
      <SketchCanvas
        key={pageId}
        width={gw}
        height={gh}
        guides={guidesFromLayout(b.layout)}
        initialUrl={sketchSeed}
        onSave={(blob) => sketchSaver.save(bookId, pageId, blob)}
        onUploadReference={(file) => uploadReference(bookId, file)}
      />
    );
  } else if (mode === "line") {
    stage = qa ? (
      <LineFixStage
        bookId={bookId}
        pageId={current.id}
        genId={current.selected_generation_id!}
        qa={qa}
        contentPx={contentPx}
        lineWidthPx={current.line_width_px}
        zoom={zoom}
        onZoomChange={setZoom}
      />
    ) : qaQ.isError ? (
      <p role="alert" className={styles.stageMsg}>
        {errMsg(qaQ.error, "無法載入線稿")}
      </p>
    ) : (
      <p className={styles.stageMsg} aria-busy="true">
        線稿計算中…
      </p>
    );
  } else {
    stage = <QACompare book={b} page={current} qa={qa} generation={selectedGen} />;
  }

  let panel: ReactNode;
  if (mode === "sketch") {
    panel = (
      <GeneratePanel
        key={current.id}
        page={current}
        hasCharacter={hasCharacter}
        characterHref={characterHref}
        onPatch={(body) => patchPage(current.id, body)}
        onGenerate={() => generate(current.id)}
        generating={candidates.isPending}
        activeCount={activeCount}
      />
    );
  } else if (mode === "line") {
    panel = <LinePanel key={current.id} page={current} onPatch={(body) => patchPage(current.id, body)} />;
  } else {
    panel = <QAPanel book={b} page={current} qa={qa} generation={selectedGen} />;
  }

  return shell(
    <div className={styles.ws}>
      <Filmstrip
        pages={pages}
        currentId={current.id}
        busyPageIds={busyPageIds}
        paperAspect={contentPx}
        onSelect={selectPage}
        onAdd={addPage}
        adding={createPage.isPending}
        onMove={movePage}
        onDelete={removePage}
      />
      <section aria-label="工作區" className={styles.work}>
        <StageHeader
          mode={mode}
          onModeChange={(m) => go(current.id, m)}
          hasSelection={hasSel}
          gapCount={qa?.gap_count ?? 0}
          idPrefix={idPrefix}
          panelId={panelId}
          right={mode === "line" ? <ZoomControls zoom={zoom} onZoomChange={setZoom} /> : undefined}
        />
        <div id={panelId} role="tabpanel" aria-labelledby={tabIdOf(idPrefix, mode)} className={`stage ${styles.stage}`}>
          {hasSel && current.aspect_mismatch && (
            <p className={styles.banner}>繪本尺寸已變更，這張圖的比例不同，建議重新生成</p>
          )}
          {stage}
        </div>
        {mode !== "qa" && (
          <CandidateTray
            generations={gens}
            selectedId={current.selected_generation_id}
            onSelect={(g) => void pickGen(g)}
            onToggleDiscard={toggleDiscard}
            onCancel={cancelGen}
            selecting={select.isPending || flushingSelect}
          />
        )}
      </section>
      <Inspector title={`第 ${index + 1} 頁進度`} items={items}>
        {panel}
      </Inspector>
    </div>,
  );
}
