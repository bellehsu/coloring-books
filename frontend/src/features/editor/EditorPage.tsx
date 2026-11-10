"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { DEFAULT_PLACEMENT, type Edits, type Generation, type Page, type Placement } from "@/api/client";
import {
  qk,
  useBook,
  useCompose,
  useCreatePage,
  useDeletePage,
  useEdits,
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
import { Dialog } from "@/components/ui/Dialog";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { checklist } from "@/lib/checklist";
import { createAutoCompose } from "./autoCompose";
import { ComposePreview, ComposeStage, ComposeStatus } from "./ComposeStage";
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
const hasBoth = (p: Page) => p.background_generation_id != null && p.pose_generation_id != null;

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

interface AutoComposeDeps {
  compose: (pageId: number) => Promise<void>;
  hasLineEdits: (pageId: number) => boolean;
  /** 要問這頁「清掉補過的線？」，回傳使用者的選擇（等到那頁在畫面上才問）。 */
  ask: (pageId: number) => Promise<boolean>;
}

/** 編輯器的 autoCompose：只建立一次（計時跨 render 與換頁），回呼由 effect 換成最新 render 的版本。 */
function createEditorAutoCompose() {
  let deps: AutoComposeDeps = { compose: async () => {}, hasLineEdits: () => true, ask: async () => false };
  const ac = createAutoCompose({
    compose: (pageId) => deps.compose(pageId),
    hasLineEdits: (pageId) => deps.hasLineEdits(pageId),
    confirm: (pageId) => deps.ask(pageId),
  });
  return { ac, setDeps: (d: AutoComposeDeps) => void (deps = d) };
}

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
  // 補過的線（合成會換掉選定圖、線跟著不見）：構圖時自動合成前要知道有沒有。補線模式由 LineFixStage 載入同一份快取
  const editsQ = useEdits(current?.id ?? 0, current != null && hasSel && mode === "compose");

  const createPage = useCreatePage(bookId);
  const updatePage = useUpdatePage(bookId);
  const deletePage = useDeletePage(bookId);
  const reorder = useReorderPages(bookId);
  const selectBg = useSelectBackground(bookId);
  const selectPose = useSelectPose(bookId);
  const compose = useCompose(bookId);

  const qc = useQueryClient();

  // ---------- 角色位置：自動存（每頁依序、只保留最新）；畫面用這個 session 最後存的值 ----------
  // 已刪除的頁 id（id 被沿用時要清掉舊狀態，見 forgetPage）也由 placementSaver 記錄（isGone）
  const placementSaver = placementSaverFor(qc);
  useEffect(() => {
    placementSaver.setErrorHandler((e) => toast.show(errMsg(e, "位置儲存失敗"), "error"));
  }, [placementSaver, toast]);
  const savedPlacement = useSyncExternalStore(
    placementSaver.subscribe,
    () => (current ? placementSaver.latest(current.id) : undefined),
    () => undefined,
  );
  const placementFailed = useSyncExternalStore(
    placementSaver.subscribe,
    () => (current ? placementSaver.failed(current.id) : false),
    () => false,
  );

  // 關分頁／重新整理：位置或修線還沒存好（送出中、失敗）就請瀏覽器確認
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => onBeforeUnload(qc, e);
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [qc]);

  const [zoom, setZoom] = useState(1);
  /** 改選背景／角色動作或合成前，正在等舊選圖的修線寫入（縮圖暫時不能點）。 */
  const [flushingSelect, setFlushingSelect] = useState(false);
  /** 非同步流程結束時用來判斷使用者是否已換頁。 */
  const currentIdRef = useRef<number | null>(null);
  useEffect(() => {
    currentIdRef.current = current?.id ?? null;
  });

  /**
   * 先讓這頁選定圖的修線寫完：改選背景／動作（合成圖會被清掉）或合成新的之後才到的 PUT
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

  // ---------- 自動合成（§4.5）：位置或素材變了 → debounce → 等修線與位置寫完 → POST compose ----------
  const pageOf = (pageId: number) => qc.getQueryData<Page[]>(qk.pages(bookId))?.find((p) => p.id === pageId);
  const composeNow = async (pageId: number) => {
    if (placementSaver.isGone(pageId)) return; // 頁面已刪除
    const page = pageOf(pageId);
    if (page) await flushSelectedEdits(page);
    try {
      await compose.mutateAsync({ pageId });
    } catch (e) {
      // 位置寫入失敗有自己的持續提示（位置尚未儲存＋重試），不再多跳 toast
      if (!placementSaver.failed(pageId)) toast.show(errMsg(e, "無法合成"), "error");
      throw e;
    }
  };
  /** 這頁目前的選定圖有沒有補過的線；還不知道（修線圖層還沒載入）就當作有，寧可多問一次。 */
  const hasLineEdits = (pageId: number) => {
    const sel = pageOf(pageId)?.selected_generation_id;
    if (sel == null) return false;
    const local = peekEditsQueue(qc, pageId, sel)?.latest;
    if (local) return local.length > 0;
    const data = qc.getQueryData<Edits>(qk.edits(pageId));
    return data?.generation_id === sel ? data.strokes.length > 0 : true;
  };
  /**
   * 等著問「清掉補過的線？」的頁 → 回覆給 autoCompose 的函式。每頁同時最多一個（每頁一次只跑一個合成）。
   * 只在那頁是目前頁時開對話框：拖完馬上換頁，要等使用者回到那頁才問（那頁的合成也等著）。
   */
  const [lossAsks, setLossAsks] = useState<ReadonlyMap<number, (ok: boolean) => void>>(() => new Map());
  const [auto] = useState(createEditorAutoCompose);
  const autoCompose = auto.ac;
  useEffect(() => {
    auto.setDeps({
      compose: composeNow,
      hasLineEdits,
      ask: (pageId) => new Promise<boolean>((resolve) => setLossAsks((m) => new Map(m).set(pageId, resolve))),
    });
  });
  useEffect(() => autoCompose.connect(), [autoCompose]);

  // 換選圖後，舊選圖閒置的修線佇列就不需要了；已刪除的 id 又出現在清單（被新頁沿用）→ 當成全新的頁
  useEffect(() => {
    for (const p of pagesQ.data ?? []) {
      if (placementSaver.isGone(p.id)) {
        forgetPageId(qc, p.id);
        autoCompose.forget(p.id);
      }
      pruneEditsQueues(qc, p.id, p.selected_generation_id);
    }
  }, [qc, placementSaver, autoCompose, pagesQ.data]);
  const composeStatus = useSyncExternalStore(
    autoCompose.subscribe,
    () => (current ? autoCompose.status(current.id) : "idle"),
    () => "idle" as const,
  );
  /** 回覆這頁的提問（使用者按了，或頁面刪除時當作取消）。 */
  const settleAsk = (pageId: number, ok: boolean) => {
    const resolve = lossAsks.get(pageId);
    if (!resolve) return;
    resolve(ok);
    setLossAsks((m) => {
      const next = new Map(m);
      next.delete(pageId);
      return next;
    });
  };
  const askingCurrent = current != null && lossAsks.has(current.id);

  // 背景與角色動作都選好、但選定圖不是它們的合成圖（「產生全部素材」自動選用、合成前離開等）：
  // 開著這頁時自動合成一次。舊流程圖片不自動換掉；有補過的線不自動問（等使用者動了才問）。
  const curGens = pageGens.data;
  const genOf = (id: number | null | undefined) => (id == null ? undefined : curGens?.find((g) => g.id === id));
  const curSel = genOf(current?.selected_generation_id);
  const autoKey =
    current &&
    hasBoth(current) &&
    current.compose_pending &&
    genOf(current.background_generation_id)?.image_url &&
    genOf(current.pose_generation_id)?.cutout_url &&
    (current.selected_generation_id == null || curSel?.kind === "composite")
      ? `${current.id}:${current.background_generation_id}:${current.pose_generation_id}:${current.selected_generation_id}`
      : null;
  const autoDone = useRef(new Set<string>());
  useEffect(() => {
    if (!autoKey || !current || autoDone.current.has(autoKey)) return;
    if (autoCompose.status(current.id) !== "idle" || hasLineEdits(current.id)) return;
    autoDone.current.add(autoKey);
    autoCompose.schedule(current.id);
    // hasLineEdits 讀快取：修線圖層載入後要再判斷一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoKey, autoCompose, editsQ.data]);
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
          autoCompose.forget(p.id);
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
    autoCompose.forget(id); // 還在等的自動合成不送
    settleAsk(id, false);
    // 先切到相鄰頁（樂觀），避免刪除完成前後閃到別頁；失敗就留在相鄰頁並提示
    if (id === current?.id && neighbor) selectPage(neighbor.id);
    deletePage.mutate(id, {
      onSuccess: () => forgetDeletedPage(qc, id),
      onError: (e) => toast.show(errMsg(e, "無法刪除頁面"), "error"),
    });
  };

  const patchPage = (pageId: number, body: PagePatch) =>
    updatePage.mutateAsync({ pageId, ...body }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });

  const picking = selectBg.isPending || selectPose.isPending || flushingSelect;

  /**
   * 縮圖選用：背景 → POST /background、角色動作 → POST /pose。合成圖只能經由合成產生。
   * 選好後兩個都有 → 自動合成。
   */
  const pickThumb = async (kind: ThumbKind, g: Generation) => {
    // 等修線寫入期間再點（連點）一律忽略，避免送出兩次
    if (!current || picking) return;
    const pageId = current.id;
    await flushSelectedEdits(current);
    const m = kind === "background" ? selectBg : selectPose;
    m.mutate(
      { pageId, generationId: g.id },
      {
        onSuccess: (page) => {
          if (hasBoth(page)) autoCompose.schedule(pageId);
        },
        onError: (e) => toast.show(errMsg(e, kind === "background" ? "無法選用這張背景" : "無法選用這個角色動作"), "error"),
      },
    );
  };

  /** 放開拖曳／縮放、按鍵、翻轉、重設位置：存位置並排自動合成。 */
  const placeCharacter = (pageId: number, p: Placement) => {
    placementSaver.savePlacement(pageId, p);
    autoCompose.schedule(pageId);
  };

  /** 到 ② 補線：這頁還有在等（或正在跑）的自動合成就先做完，補線才會畫在新的合成圖上。 */
  const toLine = async (pageId: number) => {
    const st = autoCompose.status(pageId);
    if (st === "pending" || st === "running") await autoCompose.flush(pageId);
    if (currentIdRef.current === pageId) go(pageId, "line");
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
      <Dialog open={askingCurrent} onClose={() => settleAsk(current!.id, false)} title="清掉補過的線？">
        <p className={styles.confirmText}>移動角色會清掉這頁補過的線，要繼續嗎？</p>
        <div className={styles.confirmActions}>
          <Button onClick={() => settleAsk(current!.id, false)}>取消</Button>
          <Button variant="primary" onClick={() => settleAsk(current!.id, true)}>
            繼續
          </Button>
        </div>
      </Dialog>
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
    const busyKind = (kind: Generation["kind"]) => gens.some((g) => g.kind === kind && isActive(g));
    const genSize = b.layout.generation_size;
    // 兩個都選了才能擺位置；圖片缺漏（沒有圖、沒有遮罩 → 沒有 cutout_url）由 ComposeStage 說明要重新產生
    stage =
      !hasBg && !hasPose ? (
        <p className={styles.stageMsg}>
          {busyKind("background") || busyKind("pose") ? "背景與角色動作產生中…" : "先選好背景和角色動作"}
        </p>
      ) : !hasBg || !hasPose ? (
        // 只有一個：有什麼就顯示什麼，另一個產生中或還沒選
        <ComposePreview
          backgroundUrl={backgroundGen?.image_url}
          cutoutUrl={hasPose ? poseGen?.cutout_url : null}
          aspect={genSize[0] / genSize[1]}
          message={
            !hasBg
              ? busyKind("background") ? "背景產生中…" : "先選好背景"
              : busyKind("pose") ? "角色動作產生中…" : "先選好角色動作"
          }
        />
      ) : backgroundGen && poseGen ? (
        <ComposeStage
          backgroundUrl={backgroundGen.image_url}
          cutoutUrl={poseGen.cutout_url}
          placement={placement}
          onChange={(p) => placeCharacter(current.id, p)}
          status={
            <ComposeStatus
              status={composeStatus}
              upToDate={hasSel && !current.compose_pending}
              onRetry={() => void autoCompose.flush(current.id)}
              placementFailed={placementFailed}
              onRetryPlacement={() => {
                placementSaver.retry(current.id);
                autoCompose.schedule(current.id);
              }}
            />
          }
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
          onBackgroundUploaded={(page) => {
            if (hasBoth(page)) autoCompose.schedule(page.id);
          }}
        />
        <Button
          variant="primary"
          size="lg"
          disabled={!!blockers.line}
          aria-describedby={blockers.line ? nextHintId : undefined}
          onClick={() => void toLine(current.id)}
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
          onModeChange={(m) => (m === "line" ? void toLine(current.id) : go(current.id, m))}
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
