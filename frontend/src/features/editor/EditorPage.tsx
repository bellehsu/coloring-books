"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import type { Page, Placement } from "@/api/client";
import { isActive, useBook, useEdits, useGenerations, usePages, usePagesFollowJobs, useQA, useUpdatePage } from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { checklist } from "@/lib/checklist";
import { hardAttention } from "@/lib/pageState";
import { PHONE_QUERY, useMediaQuery } from "@/lib/useMediaQuery";
import { fixHref } from "@/features/fix/fixState";
import { errMsg } from "@/features/editing/errMsg";
import { ZoomControls } from "@/features/editing/LineFixCanvas";
import { onBeforeUnload } from "@/features/editing/pendingWrites";
import { placementSaverFor } from "@/features/editing/placementSaver";
import { CaptionPreview } from "./CaptionPreview";
import { ComposePanel } from "./ComposePanel";
import { ComposeStageArea } from "./ComposeStageArea";
import { EditorShell } from "./EditorShell";
import { Filmstrip } from "./Filmstrip";
import { FinishPanel } from "./FinishPanel";
import { Inspector } from "./Inspector";
import { LineStageArea } from "./LineStageArea";
import { modeBlockers, RECOMPOSING_NOTICE, StageHeader, tabIdOf } from "./StageHeader";
import { hasBoth, useEditorAutoCompose } from "./useEditorAutoCompose";
import { gateOf, useEditorNav } from "./useEditorNav";
import { useFlushSelectedEdits } from "./useFlushSelectedEdits";
import { useMaterialPicks } from "./useMaterialPicks";
import { usePageListActions } from "./usePageListActions";
import styles from "./EditorPage.module.css";

type PagePatch = Partial<Pick<Page, "line_detail" | "line_width_px">>;

/**
 * 頁面編輯器（/books/{id}/editor?page=<id>&mode=<compose|line>；舊網址的 background／pose／qa 照常可開，見 normalizeMode）。
 * 兩步：① 構圖（舞台是背景＋去背角色，右欄是背景與角色動作卡片）→ ② 補線（修線畫布＋缺口橘色圈；右欄 FinishPanel：
 * 缺口檢查與自動補線、線條多寡、角色看起來對嗎、完成這一頁）；
 * 還沒有選定圖時補線停用（modeBlockers）。
 * 版面：BookBar＋三區 grid（> 1180 px 三欄；761–1180 兩欄、檢查面板在下；≤ 760 單欄、頁面清單橫向捲動；
 * ≤ 640 px 手機：頁面清單改成「第 N 頁 / M」下拉選單，構圖卡片收成舞台下方的精簡卡片）。
 */
export function EditorPage({ bookId }: { bookId: number }) {
  const toast = useToast();
  const idPrefix = useId();
  const panelId = `${idPrefix}-stage`;
  const phone = useMediaQuery(PHONE_QUERY);

  const book = useBook(bookId);
  const pagesQ = usePages(bookId);
  const bookGens = useGenerations(bookId);
  const hasCharacter = book.data?.character_generation_id != null;

  // 目前頁與模式：本機狀態為準（立即反應），寫回網址；網址被外部改變時跟著改
  const pages = pagesQ.data ?? [];
  const { current, mode, go, selectPage } = useEditorNav(pages);
  // 背景／角色動作完成時 worker 會自動選用：跟著重抓頁面，舞台才會填上並自動合成；
  // 目前頁等著重新合成（C 改過）時每 2 秒重抓，做好了補線才能用
  usePagesFollowJobs(bookId, bookGens.data, current?.id ?? null);
  const hasSel = current?.selected_generation_id != null;

  // 本頁所有種類（背景、角色動作、合成、舊流程 page）：找選定圖與選用的背景、動作
  const pageGens = useGenerations(bookId, { pageId: current?.id, includeDiscarded: true });
  const qaQ = useQA(current?.id ?? 0, current != null && hasSel);
  const qa = hasSel ? qaQ.data : undefined;
  // 補過的線（合成會換掉選定圖、線跟著不見）：構圖時自動合成前要知道有沒有。補線模式由 LineFixStage 載入同一份快取
  const editsQ = useEdits(current?.id ?? 0, current != null && hasSel && mode === "compose");

  const updatePage = useUpdatePage(bookId);

  const qc = useQueryClient();

  // 角色位置：自動存（每頁依序、只保留最新）；已刪除的頁 id（id 被沿用時要清掉舊狀態，見 forgetPage）也由它記錄（isGone）
  const placementSaver = placementSaverFor(qc);
  useEffect(() => {
    placementSaver.setErrorHandler((e) => toast.show(errMsg(e, "位置儲存失敗"), "error"));
  }, [placementSaver, toast]);

  // 關分頁／重新整理：位置或修線還沒存好（送出中、失敗）就請瀏覽器確認
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => onBeforeUnload(qc, e);
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [qc]);

  const [zoom, setZoom] = useState(1);
  const { flushing, flushSelectedEdits } = useFlushSelectedEdits();
  /** 非同步流程結束時用來判斷使用者是否已換頁。 */
  const currentIdRef = useRef<number | null>(null);
  useEffect(() => {
    currentIdRef.current = current?.id ?? null;
  });

  const { autoCompose, composeStatus, settleAsk, settle, settleAll, deleting, lossDialog } = useEditorAutoCompose({
    bookId,
    pages: pagesQ.data,
    current,
    currentGens: pageGens.data,
    currentEdits: editsQ.data,
    flushSelectedEdits,
  });
  const { picking, pickThumb } = useMaterialPicks({ bookId, current, flushing, flushSelectedEdits, autoCompose });
  const list = usePageListActions({ bookId, pages, current, go, selectPage, autoCompose, settleAsk, deleting });
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

  const busyPageIds = new Set(
    (bookGens.data ?? []).filter(isActive).map((g) => g.page_id).filter((x): x is number => x != null),
  );

  // ---------- 動作 ----------
  const patchPage = (pageId: number, body: PagePatch) =>
    updatePage.mutateAsync({ pageId, ...body }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });

  /** 放開拖曳／縮放、按鍵、翻轉、重設位置：存位置並排自動合成。 */
  const placeCharacter = (pageId: number, p: Placement) => {
    placementSaver.savePlacement(pageId, p);
    autoCompose.schedule(pageId);
  };

  /** 到 ② 補線：這頁還有在等（或正在跑）的自動合成就先做完，補線才會畫在新的合成圖上。 */
  const toLine = async (pageId: number) => {
    const pending = settle(pageId);
    if (pending) await pending;
    if (currentIdRef.current === pageId) go(pageId, "line");
  };

  const shell = (body: ReactNode) => (
    <EditorShell
      book={b}
      hasCharacter={hasCharacter}
      exportOpen={exportOpen}
      onExportOpenChange={setExportOpen}
      settingsOpen={settingsOpen}
      onSettingsOpenChange={setSettingsOpen}
      beforeExport={settleAll}
      lossDialog={lossDialog}
    >
      {body}
    </EditorShell>
  );

  if (!current) {
    return shell(
      <div className={styles.empty}>
        <h2 className={styles.emptyTitle}>這本繪本還沒有頁面</h2>
        <p className={styles.emptyText}>新增一頁，先產生背景與角色動作，再把角色擺到背景上合成。</p>
        <Button variant="primary" size="lg" icon={<Icons.plus size={18} />} disabled={list.adding} onClick={list.addPage}>
          新增頁面
        </Button>
      </div>,
    );
  }

  const index = pages.indexOf(current);
  const gens = pageGens.data ?? [];
  const byId = (id: number | null) => (id == null ? undefined : gens.find((g) => g.id === id));
  const selectedGen = byId(current.selected_generation_id);
  const backgroundGen = byId(current.background_generation_id);
  const poseGen = byId(current.pose_generation_id);
  const items = checklist(current, qa, { selectedKind: selectedGen?.kind });
  const contentPx = b.layout.content_px;
  const blockers = modeBlockers(gateOf(current));

  let stage: ReactNode;
  let panel: ReactNode;
  if (mode === "compose") {
    stage = (
      <ComposeStageArea
        page={current}
        gensQ={pageGens}
        backgroundGen={backgroundGen}
        poseGen={poseGen}
        generationSize={b.layout.generation_size}
        composeStatus={composeStatus}
        upToDate={hasSel && !current.compose_pending}
        onPlace={(p) => placeCharacter(current.id, p)}
        onRetryCompose={() => void autoCompose.flush(current.id)}
        onRetryPlacement={() => {
          placementSaver.retry(current.id);
          autoCompose.schedule(current.id);
        }}
      />
    );
    panel = (
      <ComposePanel
        bookId={bookId}
        page={current}
        background={backgroundGen}
        pose={poseGen}
        onPick={(kind, g) => void pickThumb(kind, g)}
        picking={picking}
        beforeReplaceBackground={() => flushSelectedEdits(current)}
        onBackgroundUploaded={(page) => {
          if (hasBoth(page)) autoCompose.schedule(page.id);
        }}
        compact={phone}
        lineBlocker={blockers.line}
        hintId={`${idPrefix}-next`}
        onNext={() => void toLine(current.id)}
      />
    );
  } else {
    stage = (
      <LineStageArea bookId={bookId} page={current} qa={qa} qaQ={qaQ} contentPx={contentPx} zoom={zoom} onZoomChange={setZoom} />
    );
    panel = (
      <FinishPanel
        book={b}
        page={current}
        qa={qa}
        onPatch={(body) => patchPage(current.id, body)}
        onGoCompose={() => go(current.id, "compose")}
      />
    );
  }

  return shell(
    <div className={styles.ws}>
      <Filmstrip
        pages={pages}
        currentId={current.id}
        busyPageIds={busyPageIds}
        paperAspect={contentPx}
        onSelect={selectPage}
        onAdd={list.addPage}
        adding={list.adding}
        onMove={list.movePage}
        onDelete={list.removePage}
        compact={phone}
      />
      <section aria-label="工作區" className={styles.work}>
        <StageHeader
          mode={mode}
          onModeChange={(m) => (m === "line" ? void toLine(current.id) : go(current.id, m))}
          blockers={blockers}
          gapCount={qa?.gap_count ?? 0}
          notice={
            !current.recompose_pending ? undefined : hardAttention(current) ? (
              <>
                重新合成沒成功，請到<Link href={`/books/${bookId}/page/${current.id}`}>處理頁面</Link>按「再試一次」
              </>
            ) : (
              RECOMPOSING_NOTICE
            )
          }
          idPrefix={idPrefix}
          panelId={panelId}
          backHref={fixHref(bookId, current.id)}
          right={mode === "line" ? <ZoomControls zoom={zoom} onZoomChange={setZoom} /> : undefined}
        />
        <div id={panelId} role="tabpanel" aria-labelledby={tabIdOf(idPrefix, mode)} className={`stage ${styles.stage}`}>
          {hasSel && current.aspect_mismatch && (
            <p className={styles.banner}>繪本尺寸已變更，這張圖的比例不同，建議重新生成</p>
          )}
          {stage}
        </div>
        <CaptionPreview page={current} />
      </section>
      <Inspector title={`第 ${index + 1} 頁進度`} items={items} checklistLast={phone}>
        {panel}
      </Inspector>
    </div>,
  );
}
