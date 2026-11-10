"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { DEFAULT_PLACEMENT, type Generation, type Page, type Placement } from "@/api/client";
import {
  useBook,
  useCompose,
  useCreatePage,
  useDeletePage,
  useGenerations,
  usePages,
  useQA,
  useReorderPages,
  useSelectBackground,
  useSelectPose,
  useUpdatePage,
} from "@/api/hooks";
import { AppBar } from "@/components/shell/AppBar";
import { BookBar } from "@/components/shell/BookBar";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { checklist } from "@/lib/checklist";
import { ComposePanel } from "./ComposePanel";
import { ComposeStage } from "./ComposeStage";
import { ComposeWorkspace } from "./ComposeWorkspace";
import { peekEditsQueue, pruneEditsQueues } from "./editsQueue";
import { forgetDeletedPage, forgetPageId } from "./forgetPage";
import { errMsg } from "./errMsg";
import { BookSettingsDialog } from "@/features/books/BookSettingsDialog";
import { ExportDrawer } from "./ExportDrawer";
import { Filmstrip } from "./Filmstrip";
import { Inspector } from "./Inspector";
import { LineFixStage } from "./LineFixStage";
import { LinePanel } from "./LinePanel";
import { onBeforeUnload } from "./pendingWrites";
import { placementSaverFor } from "./placementSaver";
import { QAPanel } from "./QAPanel";
import { sketchSaverFor } from "./sketchSaver";
import type { ThumbKind } from "./Thumbs";
import {
  allowedMode,
  DEFAULT_MODE,
  modeBlockers,
  normalizeMode,
  StageHeader,
  tabIdOf,
  type EditorMode,
  type ModeGate,
} from "./StageHeader";
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
    mode: normalizeMode(m),
  };
}

type PagePatch = Partial<Pick<Page, "line_detail" | "line_width_px">>;

/**
 * 頁面編輯器（/books/{id}/editor?page=<id>&mode=<compose|line>；舊網址的 background／pose／qa 照常可開，見 normalizeMode）。
 * 兩步：① 構圖（舞台是背景＋去背角色，右欄是背景與角色動作卡片）→ ② 補線（修線畫布、線條設定與 QA）；
 * 還沒有選定圖時補線停用（modeBlockers）。
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
  const bookGens = useGenerations(bookId);
  const hasCharacter = book.data?.character_generation_id != null;

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
  const gateOf = (p: Page | null | undefined): ModeGate => ({ selectedId: p?.selected_generation_id ?? null });
  const mode: EditorMode = allowedMode(nav.mode, gateOf(current));

  const go = (page: number, m: EditorMode) => {
    setNav({ page, mode: m });
    const q = new URLSearchParams();
    q.set("page", String(page));
    if (m !== DEFAULT_MODE) q.set("mode", m);
    const next = q.toString();
    if (next !== qs) setWrites((w) => [...w, next]);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  /** 換頁：目標頁不能用目前的模式就回到構圖（否則之後條件成立時會突然跳過去）。 */
  const selectPage = (id: number) => {
    const target = pages.find((p) => p.id === id);
    go(id, allowedMode(mode, gateOf(target)));
  };

  // 本頁所有種類（背景、角色動作、合成、舊流程 page）：找選定圖與選用的背景、動作
  const pageGens = useGenerations(bookId, { pageId: current?.id, includeDiscarded: true });
  const qaQ = useQA(current?.id ?? 0, current != null && hasSel);
  const qa = hasSel ? qaQ.data : undefined;

  const createPage = useCreatePage(bookId);
  const updatePage = useUpdatePage(bookId);
  const deletePage = useDeletePage(bookId);
  const reorder = useReorderPages(bookId);
  const selectBg = useSelectBackground(bookId);
  const selectPose = useSelectPose(bookId);
  const compose = useCompose(bookId);

  // 刪除中／已刪除的頁 id（id 被沿用時要清掉舊狀態，見 forgetPage）由 sketchSaver 記錄；編輯器不再有草圖畫板
  const qc = useQueryClient();
  const sketchSaver = sketchSaverFor(qc);

  // ---------- 角色位置：自動存（每頁依序、只保留最新）；畫面用這個 session 最後存的值 ----------
  const placementSaver = placementSaverFor(qc);
  useEffect(() => {
    placementSaver.setErrorHandler((e) => toast.show(errMsg(e, "位置儲存失敗"), "error"));
  }, [placementSaver, toast]);
  const savedPlacement = useSyncExternalStore(
    placementSaver.subscribe,
    () => (current ? placementSaver.latest(current.id) : undefined),
    () => undefined,
  );

  // 關分頁／重新整理：草圖或修線還沒存好（送出中、失敗、畫布 debounce 中）就請瀏覽器確認
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => onBeforeUnload(qc, e);
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [qc]);

  // 換選圖後，舊選圖閒置的修線佇列就不需要了；已刪除的 id 又出現在清單（被新頁沿用）→ 當成全新的頁
  useEffect(() => {
    for (const p of pagesQ.data ?? []) {
      if (sketchSaver.isGone(p.id)) forgetPageId(qc, p.id);
      pruneEditsQueues(qc, p.id, p.selected_generation_id);
    }
  }, [qc, sketchSaver, pagesQ.data]);

  const [zoom, setZoom] = useState(1);
  /** 改選背景／角色動作或合成前，正在等舊選圖的修線寫入（縮圖暫時不能點）。 */
  const [flushingSelect, setFlushingSelect] = useState(false);
  /**
   * 正在確認合成（等修線 → 等位置 → POST compose）的頁面 id。以頁為單位：合成中換頁，另一頁照常可用。
   * ref 擋住同一輪 render 前的第二次點擊。
   */
  const [confirmingIds, setConfirmingIds] = useState<ReadonlySet<number>>(() => new Set());
  const confirmingRef = useRef(new Set<number>());
  /** 非同步流程結束時用來判斷使用者是否已換頁。 */
  const currentIdRef = useRef<number | null>(null);
  useEffect(() => {
    currentIdRef.current = current?.id ?? null;
  });
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

  const characterHref = `/books/${bookId}/character`;
  const busyPageIds = new Set(
    (bookGens.data ?? []).filter(isActive).map((g) => g.page_id).filter((x): x is number => x != null),
  );

  // ---------- 動作 ----------
  const addPage = () =>
    createPage.mutate(
      { scene_prompt: "", action_prompt: "" },
      {
        onSuccess: (p) => {
          forgetPageId(qc, p.id); // 後端若沿用了已刪除頁的 id，不要讓舊頁的狀態串過來
          go(p.id, DEFAULT_MODE);
        },
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
    // 切頁前先標記刪除中：畫布卸載時 flush 的草圖先停著，不和 DELETE 賽跑、不跳出誤導的「草圖儲存失敗」
    sketchSaver.beginDelete(id);
    // 先切到相鄰頁（樂觀），避免刪除完成前後閃到別頁；失敗就留在相鄰頁並提示
    if (id === current?.id && neighbor) selectPage(neighbor.id);
    deletePage.mutate(id, {
      onSuccess: () => forgetDeletedPage(qc, id),
      onError: (e) => {
        sketchSaver.endDelete(id, false); // 停著的草圖照常送出
        toast.show(errMsg(e, "無法刪除頁面"), "error");
      },
    });
  };

  const patchPage = (pageId: number, body: PagePatch) =>
    updatePage.mutateAsync({ pageId, ...body }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });

  /**
   * 先讓目前選圖的修線寫完：改選背景／動作（合成圖會被清掉）或確認新合成後才到的 PUT
   * 會被後端以「選圖已更換」拒絕，出現多餘的「修線尚未儲存」。失敗已提示過，不擋後續動作。
   */
  const flushSelectedEdits = async (page: Page) => {
    const sel = page.selected_generation_id;
    const pendingEdits = sel != null ? peekEditsQueue(qc, page.id, sel) : undefined;
    if (!pendingEdits?.busy()) return;
    setFlushingSelect(true);
    try {
      await pendingEdits.flush();
    } catch {
      /* 失敗已提示過 */
    } finally {
      setFlushingSelect(false);
    }
  };

  const picking = selectBg.isPending || selectPose.isPending || flushingSelect;

  /** 縮圖選用：背景 → POST /background、角色動作 → POST /pose。合成圖永遠不走 /select。 */
  const pickThumb = async (kind: ThumbKind, g: Generation) => {
    // 等修線寫入期間再點（連點）一律忽略，避免送出兩次
    if (!current || picking) return;
    const pageId = current.id;
    await flushSelectedEdits(current);
    const m = kind === "background" ? selectBg : selectPose;
    m.mutate(
      { pageId, generationId: g.id },
      { onError: (e) => toast.show(errMsg(e, kind === "background" ? "無法選用這張背景" : "無法選用這個角色動作"), "error") },
    );
  };

  const savePlacement = (pageId: number, p: Placement) => placementSaver.savePlacement(pageId, p);

  /** 確認合成：等舊選圖的修線 → useCompose（內含等位置寫完）→ 成功後切到補線（還在這頁時）。 */
  const confirmCompose = async () => {
    if (!current || confirmingRef.current.has(current.id)) return;
    const pageId = current.id;
    confirmingRef.current.add(pageId);
    setConfirmingIds(new Set(confirmingRef.current));
    try {
      await flushSelectedEdits(current);
      await compose.mutateAsync({ pageId });
      if (currentIdRef.current === pageId) go(pageId, "line");
    } catch (e) {
      // useCompose 在位置寫入失敗時丟一般 Error（不是 ApiError），errMsg 會吃掉它的訊息
      const msg = placementSaver.failed(pageId) ? "位置尚未儲存，沒有合成；請重試" : errMsg(e, "無法合成");
      toast.show(msg, "error");
    } finally {
      confirmingRef.current.delete(pageId);
      setConfirmingIds(new Set(confirmingRef.current));
    }
  };

  const shell = (body: ReactNode) => (
    <>
      <AppBar crumbs={[{ label: "我的繪本", href: "/" }, { label: b.title }]} />
      <BookBar book={b} current="pages" onExport={() => setExportOpen(true)} onSettings={() => setSettingsOpen(true)} />
      {!hasCharacter && (
        <p className={styles.notice}>
          這本繪本還沒設定角色，不能產生角色動作。<Link href={characterHref}>前往角色工作室</Link>
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
        <p className={styles.emptyText}>新增一頁，先產生背景與角色動作，再把角色擺到背景上合成。</p>
        <Button variant="primary" size="lg" icon={<Icons.plus size={18} />} disabled={createPage.isPending} onClick={addPage}>
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
  const confirming = confirmingIds.has(current.id);
  const contentPx = b.layout.content_px;
  const placement: Placement = savedPlacement ?? current.placement ?? DEFAULT_PLACEMENT;
  const loadingMsg = pageGens.isError ? (
    <p role="alert" className={styles.stageMsg}>
      {errMsg(pageGens.error, "無法載入圖片")}
    </p>
  ) : (
    <p className={styles.stageMsg} aria-busy="true">
      載入中…
    </p>
  );

  const blockers = modeBlockers(gateOf(current));
  const nextHintId = `${idPrefix}-next`;

  let stage: ReactNode;
  let panel: ReactNode;
  if (mode === "compose") {
    const hasBg = current.background_generation_id != null;
    const hasPose = current.pose_generation_id != null;
    // 兩個都選了才有舞台；圖片缺漏（沒有圖、沒有遮罩 → 沒有 cutout_url）由 ComposeStage 說明要重新產生
    stage =
      !hasBg || !hasPose ? (
        <p className={styles.stageMsg}>
          {!hasBg && !hasPose ? "先選好背景和角色動作" : !hasBg ? "先選好背景" : "先選好角色動作"}
        </p>
      ) : backgroundGen && poseGen ? (
        <ComposeStage
          backgroundUrl={backgroundGen.image_url}
          cutoutUrl={poseGen.cutout_url}
          placement={placement}
          onChange={(p) => savePlacement(current.id, p)}
          disabled={confirming}
        />
      ) : pageGens.data && !pageGens.isFetching ? (
        <p role="alert" className={styles.stageMsg}>
          找不到選用的背景或角色動作，請重新選一張
        </p>
      ) : (
        loadingMsg
      );
    panel = (
      <>
        <ComposeWorkspace
          key={current.id}
          bookId={bookId}
          page={current}
          background={backgroundGen}
          pose={poseGen}
          onPickBackground={(g) => void pickThumb("background", g)}
          onPickPose={(g) => void pickThumb("pose", g)}
          picking={picking}
          beforeReplaceBackground={() => flushSelectedEdits(current)}
        />
        {hasBg && hasPose && (
          <ComposePanel
            key={`compose-${current.id}`}
            page={current}
            placement={placement}
            onPlacementChange={(p) => savePlacement(current.id, p)}
            onConfirm={confirmCompose}
            confirming={confirming}
          />
        )}
        <Button
          variant="primary"
          size="lg"
          disabled={!!blockers.line}
          aria-describedby={blockers.line ? nextHintId : undefined}
          onClick={() => go(current.id, "line")}
        >
          下一步：補線 →
        </Button>
        {blockers.line && (
          <span id={nextHintId} className={styles.nextHint}>
            {blockers.line}
          </span>
        )}
      </>
    );
  } else {
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
    panel = (
      <>
        <LinePanel key={current.id} page={current} onPatch={(body) => patchPage(current.id, body)} />
        <QAPanel book={b} page={current} qa={qa} generation={selectedGen} />
      </>
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
        onAdd={addPage}
        adding={createPage.isPending}
        onMove={movePage}
        onDelete={removePage}
      />
      <section aria-label="工作區" className={styles.work}>
        <StageHeader
          mode={mode}
          onModeChange={(m) => go(current.id, m)}
          blockers={blockers}
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
      </section>
      <Inspector title={`第 ${index + 1} 頁進度`} items={items}>
        {panel}
      </Inspector>
    </div>,
  );
}
