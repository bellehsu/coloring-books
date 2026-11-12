"use client";

import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent, type CSSProperties, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { DEFAULT_PLACEMENT, type Book, type Generation, type OverviewPage, type Page, type Placement, type QA } from "@/api/client";
import {
  qk,
  useAutofix,
  useBook,
  useBookOverview,
  useGenerations,
  usePagesFollowJobs,
  useQA,
  recomposeRunning,
} from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { CaptionField, captionError } from "@/components/ui/CaptionField";
import { Segmented } from "@/components/ui/Segmented";
import { useToast } from "@/components/ui/Toast";
import { STATE_LABEL } from "@/features/book/overviewState";
import { ComposeStage } from "@/features/editor/ComposeStage";
import { ExclusiveChips, usePageText } from "@/features/editor/ComposeWorkspace";
import chip from "@/features/editor/Inspector.module.css";
import { flushAllEditsFor, peekEditsQueue } from "@/features/editor/editsQueue";
import { errMsg } from "@/features/editor/errMsg";
import { LineFixStage } from "@/features/editor/LineFixStage";
import { onBeforeUnload } from "@/features/editor/pendingWrites";
import { ACTION_HINT, EXPRESSIONS, FACING_HINT, FACINGS } from "@/lib/actions";
import { MAX_ACTION, MAX_SCENE, normalizeCaption } from "@/lib/limits";
import type { StrokeOp } from "@/lib/strokes";
import { UPLOAD_ACCEPT } from "@/lib/upload";
import {
  useAcceptPage,
  useCaptionDisplay,
  useFixPages,
  usePatchPageC,
  useRedoPage,
  useRegenerate,
  useRestorePage,
  useRetryPage,
  useSavePlacementC,
  useUndoPage,
  useUploadBackgroundC,
  refreshFix,
} from "./fixApi";
import {
  captionChoiceLabel,
  captionSummary,
  fixHref,
  neighbours,
  pageState,
  pageTitle,
  penBlocked,
  printUrl,
  showRestore,
  type FixFilter,
  type RestoreItem,
} from "./fixState";
import styles from "./FixPage.module.css";

const isActive = (g: Generation) => g.status === "queued" || g.status === "running";
const pct = (v: number) => `${+(v * 100).toFixed(4)}%`;

export interface FixPageProps {
  bookId: number;
  pageId: number;
  /** 從 B 的「請看一下」打開（?filter=attention）：上一頁／下一頁與「這頁可以印了」只在要看的頁之間跳。 */
  filter: FixFilter;
}

/**
 * C 處理一頁（spec §4.4、mockups/3_fix.png）：頁首（← 整本書、標題與狀態、↶ 復原／↷ 重做、上一頁／下一頁），
 * 預覽（要印的線稿／彩色原圖、圈出沒接起來的線、拖角色），每一項的修改列（角色動作、背景、線條、圖下方的字）
 * 與「↺ 還原成自動畫的那張」，失敗時的原因卡與「再試一次」，「這頁可以印了 ✓」。
 * 所有修改都走 ?oneclick=true：後端先存復原快照，需要時重新合成，跑完仍是可以印。
 */
export function FixPage({ bookId, pageId, filter }: FixPageProps) {
  const book = useBook(bookId);
  const overview = useBookOverview(bookId);
  const gens = useGenerations(bookId, { pageId });
  usePagesFollowJobs(bookId, gens.data);
  const ovPage = overview.data?.pages.find((p) => p.id === pageId);
  const genBusy = gens.data?.some(isActive) ?? false;
  const pages = useFixPages(bookId, pageId, ovPage?.state === "working" || genBusy);
  const page = pages.data?.find((p) => p.id === pageId);

  if (!page || !book.data) {
    const err = pages.error ?? book.error;
    const missing = pages.data && !page;
    return (
      <main className={styles.main}>
        <div className={styles.loadMsg}>
          {err || missing ? (
            <>
              <p role="alert">{missing ? "找不到這一頁" : errMsg(err, "無法載入這一頁")}</p>
              <Link href={`/books/${bookId}`}>回到整本書</Link>
            </>
          ) : (
            <p aria-busy="true">載入中…</p>
          )}
        </div>
      </main>
    );
  }
  return (
    <FixBody
      bookId={bookId}
      book={book.data}
      page={page}
      filter={filter}
      gens={gens.data ?? []}
      overviewPages={overview.data?.pages}
    />
  );
}

interface BodyProps {
  bookId: number;
  book: Book;
  page: Page;
  filter: FixFilter;
  gens: Generation[];
  overviewPages: OverviewPage[] | undefined;
}

type View = "print" | "color";

function FixBody({ bookId, book, page, filter, gens, overviewPages }: BodyProps) {
  const pageId = page.id;
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const hasArt = page.selected_generation_id != null;
  const qaQ = useQA(pageId, hasArt);
  const qa = qaQ.data;
  // QA 載入失敗（例如原圖遺失 404）：畫筆與線條列說明原因，可以再試一次
  const qaError = !qa && qaQ.error ? errMsg(qaQ.error, "線稿載入失敗") : null;
  const retryQA = () => void qaQ.refetch();
  const ovPage = overviewPages?.find((p) => p.id === pageId);
  const state = pageState(page, ovPage);
  const { prev, next } = overviewPages ? neighbours(overviewPages, pageId, filter) : { prev: null, next: null };

  const undoM = useUndoPage(bookId, pageId);
  const redoM = useRedoPage(bookId, pageId);
  const restoreM = useRestorePage(bookId, pageId);
  const acceptM = useAcceptPage(bookId, pageId);
  const retryM = useRetryPage(bookId, pageId);
  const placement = usePlacementSaves(bookId, pageId);

  const [view, setView] = useState<View>("print");
  const [showGaps, setShowGaps] = useState(false);
  const [drawTool, setDrawTool] = useState<StrokeOp | null>(null);
  const [acting, setActing] = useState(false);
  const actingRef = useRef(false);
  // 復原／重做／還原成功一次就加 1：文字欄位（動作、場景、圖下方的字）丟掉改過的值、改顯示伺服器的
  const [historyEpoch, setHistoryEpoch] = useState(0);
  // 文字欄位離開時的存檔與「中文／英文」開關的 PATCH（送出中）：復原／重做／還原、可以印、再試一次先等它們
  const pendingSaves = useRef(new Set<Promise<unknown>>());
  const [textSaving, setTextSaving] = useState(false);
  const trackSave = useCallback((p: Promise<unknown>): Promise<unknown> => {
    const set = pendingSaves.current;
    set.add(p);
    setTextSaving(true);
    const done = () => {
      set.delete(p);
      setTextSaving(set.size > 0);
    };
    p.then(done, done);
    return p;
  }, []);
  const textSaves = () => Promise.allSettled([...pendingSaves.current]);

  // 關分頁／重新整理：修線（送出中、失敗）、位置或文字還沒存好就請瀏覽器確認
  const placementPending = placement.hasPending;
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => onBeforeUnload(qc, e, placementPending() || pendingSaves.current.size > 0);
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [qc, placementPending]);

  const blocked = penBlocked(page);
  const drawing = drawTool !== null && blocked === null && hasArt;
  const bgGen = gens.find((g) => g.id === page.background_generation_id);
  const poseGen = gens.find((g) => g.id === page.pose_generation_id);
  const bgActive = gens.some((g) => g.kind === "background" && isActive(g));
  const poseActive = gens.some((g) => g.kind === "pose" && isActive(g));
  // 合成失敗（recompose_pending＋attention）不算在做：顯示原因卡與「再試一次」
  const working = recomposeRunning(page) || state === "working";

  /** 送出前先讓畫面上的筆畫寫完（C 與進階編輯的佇列都等；否則晚到的筆畫會蓋掉復原的結果）。 */
  async function flushEdits() {
    const gid = page.selected_generation_id;
    if (gid == null) return;
    await flushAllEditsFor(qc, pageId, gid).catch(() => {});
  }

  /** 一次只做一件事（復原、重做、還原、可以印、再試一次）；失敗 toast。 */
  async function act(fn: () => Promise<unknown>, fallback: string) {
    if (actingRef.current) return;
    actingRef.current = true;
    setActing(true);
    try {
      await fn();
    } catch (e) {
      toast.show(errMsg(e, fallback), "error");
    } finally {
      actingRef.current = false;
      setActing(false);
    }
  }

  /** 復原／重做／還原：先等文字、拖拉的位置與筆畫寫完（晚到的寫入不會蓋掉復原），再收起畫筆
   * （畫布的筆畫在之後重新從伺服器載入）。 */
  const runHistory = (fn: () => Promise<unknown>, fallback: string) =>
    act(async () => {
      await textSaves();
      await placement.idle();
      await flushEdits();
      setDrawTool(null);
      await fn(); // 回傳的頁面已寫進快取（putPage），欄位重設後顯示的就是復原後的文字
      setHistoryEpoch((e) => e + 1);
    }, fallback);

  const undo = () => runHistory(() => undoM.mutateAsync(), "沒辦法復原");
  const redo = () => runHistory(() => redoM.mutateAsync(), "沒辦法重做");
  const restore = (item: RestoreItem) => runHistory(() => restoreM.mutateAsync(item), "沒辦法還原");

  const accept = () =>
    act(async () => {
      await textSaves();
      await placement.idle();
      await flushEdits();
      await acceptM.mutateAsync();
      router.push(next ? fixHref(bookId, next.id, filter) : `/books/${bookId}`);
    }, "沒辦法標成可以印");

  /** 「再試一次」：只處理這一頁（重畫失敗的那張，或重跑這頁的流程）。 */
  const retry = () =>
    act(async () => {
      await textSaves();
      const r = await retryM.mutateAsync();
      if (r.requeued.length > 0) {
        const what = r.requeued.map((k) => (k === "pose" ? "角色動作" : "背景")).join("和");
        toast.show(`重新畫這一頁的${what}了，好了會自動換上去`, "info");
      } else toast.show("重新做這一頁了，好了會自動更新", "info");
    }, "沒辦法再試一次");

  // 修改列的按鈕不等文字存檔：「換一張」「AI 再畫一張」自己會先存好文字，停用反而會吃掉「改完直接按」的那一下
  const rowBusy = acting || undoM.isPending || redoM.isPending || placement.saving;
  const busy = rowBusy || textSaving;
  const acceptBlocked = !page.recompose_pending
    ? null
    : page.attention === "failed"
      ? "這一頁合成失敗，請先按「再試一次」再印"
      : "這一頁還在重新合成，等它做完再按";

  return (
    <main className={styles.main}>
      <header className={styles.head}>
        <Link href={`/books/${bookId}`} className={styles.back}>
          <span aria-hidden="true">←</span> 整本書
        </Link>
        <h1 className={styles.h1}>{pageTitle(page)}</h1>
        <span className={`${styles.pill} ${styles[`pill_${state}`]}`}>{STATE_LABEL[state]}</span>
        <div className={styles.headActions}>
          <Button className={styles.headBtn} disabled={!page.can_undo || busy} onClick={() => void undo()}>
            <span aria-hidden="true">↶</span> 復原
          </Button>
          <Button className={styles.headBtn} disabled={!page.can_redo || busy} onClick={() => void redo()}>
            <span aria-hidden="true">↷</span> 重做
          </Button>
          <nav aria-label="換頁" className={styles.pager}>
            <PagerLink href={prev ? fixHref(bookId, prev.id, filter) : null} label="上一頁" arrow="‹" before />
            <PagerLink href={next ? fixHref(bookId, next.id, filter) : null} label="下一頁" arrow="›" />
          </nav>
        </div>
      </header>

      <div className={styles.grid}>
        <section aria-label="預覽" className={styles.previewCard}>
          <div className={styles.previewBar}>
            <Segmented<View>
              kind="radio"
              label="預覽"
              value={view}
              onChange={(v) => {
                setView(v);
                setDrawTool(null);
              }}
              options={[
                { value: "print", label: "要印的線稿" },
                { value: "color", label: "彩色原圖" },
              ]}
            />
            {view === "print" && hasArt && !drawing && qa && (
              <label className={styles.gapToggle}>
                <input
                  type="checkbox"
                  checked={showGaps}
                  disabled={qa.gap_count === 0}
                  onChange={(e) => setShowGaps(e.target.checked)}
                />
                圈出沒接起來的線（{qa.gap_count} 處）
              </label>
            )}
          </div>
          <div className={styles.stage}>
            {drawing && page.selected_generation_id != null ? (
              <DrawStage
                bookId={bookId}
                page={page}
                genId={page.selected_generation_id}
                qa={qa}
                qaError={qaError}
                onRetryQA={retryQA}
                contentPx={book.layout.content_px}
                tool={drawTool}
                onDone={() => setDrawTool(null)}
              />
            ) : view === "print" ? (
              <PrintPreview page={page} qa={qa} showGaps={showGaps} contentPx={book.layout.content_px} working={working} />
            ) : (
              <ColorPreview page={page} bgGen={bgGen} poseGen={poseGen} draft={placement.draft} onChange={placement.save} />
            )}
          </div>
          {/* 常駐的 live region：文字是之後放進去的，螢幕閱讀器才會唸 */}
          <p role="status" className={styles.working}>
            {working && "正在重新做這一頁…好了會自動更新"}
          </p>
        </section>

        <div className={styles.side}>
          {page.attention === "failed" && <ReasonCard detail={page.attention_detail} busy={busy} onRetry={() => void retry()} />}
          <ul className={styles.items} aria-label="這一頁的東西">
            <PoseRow
              bookId={bookId}
              page={page}
              hasCharacter={book.character_generation_id != null}
              active={poseActive}
              flushEdits={flushEdits}
              restore={showRestore(page, "pose") ? () => void restore("pose") : null}
              busy={rowBusy}
              textKey={historyEpoch}
              trackSave={trackSave}
            />
            <BackgroundRow
              bookId={bookId}
              page={page}
              bgGen={bgGen}
              active={bgActive}
              flushEdits={flushEdits}
              restore={showRestore(page, "background") ? () => void restore("background") : null}
              busy={rowBusy}
              textKey={historyEpoch}
              trackSave={trackSave}
            />
            <LinesRow
              bookId={bookId}
              page={page}
              qa={qa}
              qaError={qaError}
              onRetryQA={retryQA}
              blocked={blocked}
              tool={drawing ? drawTool : null}
              onTool={(t) => {
                setView("print");
                setDrawTool(t);
              }}
              restore={showRestore(page, "lines") ? () => void restore("lines") : null}
              busy={rowBusy}
            />
            <CaptionRow
              bookId={bookId}
              page={page}
              textKey={historyEpoch}
              busy={rowBusy || page.recompose_pending}
              trackSave={trackSave}
            />
          </ul>
          {hasArt && qa && qa.gap_count > 0 && (
            <p className={styles.note}>線沒接起來也可以印；想讓顏色不漏出去，再按「自動補線」或用畫筆接起來。</p>
          )}
          {hasArt && (
            <Button size="lg" className={styles.accept} disabled={busy || acceptBlocked !== null} onClick={() => void accept()}>
              這頁可以印了 ✓
            </Button>
          )}
          {hasArt && acceptBlocked && <p className={styles.rowWarn}>{acceptBlocked}</p>}
          <Link href={`/books/${bookId}/editor?page=${pageId}`} className={styles.advanced}>
            進階編輯
          </Link>
        </div>
      </div>
    </main>
  );
}

function PagerLink({ href, label, arrow, before }: { href: string | null; label: string; arrow: string; before?: boolean }) {
  const text = <span className={styles.pagerText}>{label}</span>;
  const content = before ? (
    <>
      <span aria-hidden="true">{arrow}</span> {text}
    </>
  ) : (
    <>
      {text} <span aria-hidden="true">{arrow}</span>
    </>
  );
  if (!href)
    return (
      <span className={`${styles.pagerLink} ${styles.pagerOff}`} aria-disabled="true">
        {content}
      </span>
    );
  return (
    <Link href={href} className={styles.pagerLink}>
      {content}
    </Link>
  );
}

function ReasonCard({ detail, busy, onRetry }: { detail: string | null; busy: boolean; onRetry: () => void }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className={styles.reason}>
      <h2 id={id} className={styles.reasonKicker}>
        為什麼請你看
      </h2>
      <p className={styles.reasonText}>{detail || "這一頁沒做好"}</p>
      <p className={styles.reasonHint}>按「再試一次」重新做這一頁；也可以換一張圖或上傳背景。</p>
      <Button variant="primary" disabled={busy} onClick={onRetry}>
        再試一次
      </Button>
    </section>
  );
}

// ---------- 預覽 ----------

function PrintPreview({
  page,
  qa,
  showGaps,
  contentPx,
  working,
}: {
  page: Page;
  qa: QA | undefined;
  showGaps: boolean;
  contentPx: readonly [number, number];
  working: boolean;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  if (page.selected_generation_id == null || !page.lineart_url) {
    return (
      <p className={styles.empty}>
        {working ? "製作中…好了會自動出現" : "這一頁還沒有圖。可以用右邊的按鈕畫一張，或回到整本書按「繼續做」。"}
      </p>
    );
  }
  if (showGaps && qa) {
    const [w, h] = contentPx;
    return (
      <div className={styles.paper} style={{ "--ar": w / h } as CSSProperties}>
        {/* eslint-disable-next-line @next/next/no-img-element -- 後端動態 PNG */}
        <img src={page.lineart_url} alt="線稿，橘色圈標出沒接起來的線" className={styles.paperImg} draggable={false} />
        {qa.gaps.map(([x, y], i) => (
          <span
            key={`${i}-${x}-${y}`}
            aria-hidden="true"
            data-testid="gap-marker"
            className={styles.gap}
            style={{ left: pct(x), top: pct(y) }}
          />
        ))}
      </div>
    );
  }
  const src = printUrl(page);
  if (failed === src) return <p className={styles.empty}>列印頁預覽載入失敗，請重新整理</p>;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- 後端動態 PNG（帶 ?v=）
    <img src={src} alt="要印的線稿" className={styles.printImg} draggable={false} onError={() => setFailed(src)} />
  );
}

/** 方向鍵／＋－ 停下來多久才存（按住時鍵盤自動重複約每秒 30 次，放開後只存一次、只有一步復原）。 */
const KEY_SAVE_DELAY_MS = 400;

interface PlacementQueue {
  /** 等著送的（最新為準）。 */
  next: Placement | null;
  /** 按鍵停下來才送的。 */
  keyed: Placement | null;
  timer: ReturnType<typeof setTimeout> | null;
  /** 正在送的那一輪（一次只有一個 PUT 在路上）。 */
  run: Promise<void> | null;
}

/**
 * 拖完角色的位置存檔（PUT /placement?oneclick=true，每次後端都存快照、重新合成）：一次只送一個，送出中又改了
 * 只留最新的一筆，等前一個回來再送（latest-wins）；方向鍵／＋－ 停下來 400 ms 才存。存好前畫面用 draft。
 * saving 時復原／重做／還原停用，idle() 讓它們（與「這頁可以印了」）先等位置寫完（按鍵等著的立刻送），
 * 晚到的位置不會落在復原之後。
 */
function usePlacementSaves(bookId: number, pageId: number) {
  const { show } = useToast();
  const { mutateAsync } = useSavePlacementC(bookId, pageId);
  const [draft, setDraft] = useState<Placement | null>(null);
  const [saving, setSaving] = useState(false);
  const q = useRef<PlacementQueue>({ next: null, keyed: null, timer: null, run: null });

  const kick = useCallback(
    function kick() {
      const s = q.current;
      if (s.run) return;
      s.run = (async () => {
        while (s.next) {
          const p = s.next;
          s.next = null;
          try {
            await mutateAsync(p);
          } catch (e) {
            show(errMsg(e, "位置沒有存起來，請再拖一次"), "error");
          }
        }
      })().finally(() => {
        s.run = null;
        if (s.next) kick();
        else if (!s.timer) {
          setDraft(null);
          setSaving(false);
        }
      });
    },
    [mutateAsync, show],
  );
  /** 按鍵等著的那一筆現在就送。 */
  const flushKey = useCallback(() => {
    const s = q.current;
    if (!s.timer) return;
    clearTimeout(s.timer);
    s.timer = null;
    s.next = s.keyed;
    s.keyed = null;
    kick();
  }, [kick]);
  // 卸載（換頁）時按鍵還沒送的也要送
  useEffect(() => flushKey, [flushKey]);

  const onChange = (p: Placement, via?: "key") => {
    const s = q.current;
    setDraft(p);
    setSaving(true);
    if (s.timer) clearTimeout(s.timer);
    s.timer = null;
    if (via === "key") {
      s.keyed = p;
      s.timer = setTimeout(flushKey, KEY_SAVE_DELAY_MS);
    } else {
      s.keyed = null;
      s.next = p;
      kick();
    }
  };
  const idle = async () => {
    flushKey();
    while (q.current.run) await q.current.run;
  };
  /** 還有位置沒存好（等著按鍵停、等著送、送出中）。 */
  const hasPending = useCallback(() => {
    const s = q.current;
    return s.timer != null || s.next != null || s.run != null;
  }, []);
  return { draft, saving, save: onChange, idle, hasPending };
}

function ColorPreview({
  page,
  bgGen,
  poseGen,
  draft,
  onChange,
}: {
  page: Page;
  bgGen: Generation | undefined;
  poseGen: Generation | undefined;
  draft: Placement | null;
  onChange: (p: Placement, via?: "key") => void;
}) {
  if (page.background_generation_id != null && page.pose_generation_id != null) {
    const bgUrl = bgGen?.image_url ?? `/api/generations/${page.background_generation_id}/image`;
    const cutUrl = poseGen ? poseGen.cutout_url : `/api/generations/${page.pose_generation_id}/cutout`;
    return (
      <div className={styles.colorWrap}>
        <ComposeStage
          backgroundUrl={bgUrl}
          cutoutUrl={cutUrl}
          placement={draft ?? (page.placement as Placement | null) ?? DEFAULT_PLACEMENT}
          onChange={onChange}
        />
      </div>
    );
  }
  if (page.selected_generation_id != null)
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={`/api/generations/${page.selected_generation_id}/image`} alt="彩色原圖" className={styles.printImg} />
    );
  return <p className={styles.empty}>這一頁還沒有圖。</p>;
}

function DrawStage({
  bookId,
  page,
  genId,
  qa,
  qaError,
  onRetryQA,
  contentPx,
  tool,
  onDone,
}: {
  bookId: number;
  page: Page;
  genId: number;
  qa: QA | undefined;
  qaError: string | null;
  onRetryQA: () => void;
  contentPx: [number, number];
  tool: StrokeOp | null;
  onDone: () => void;
}) {
  return (
    <div className={styles.drawWrap}>
      <div className={styles.drawBar}>
        <p className={styles.drawHint}>用「補線」把線接起來，「擦除」擦掉多的線。畫好會自動存。</p>
        <Button variant="sky" onClick={onDone}>
          畫好了
        </Button>
      </div>
      {qa ? (
        <LineFixStage
          key={`${genId}-${tool}`}
          bookId={bookId}
          pageId={page.id}
          genId={genId}
          qa={qa}
          contentPx={contentPx}
          lineWidthPx={page.line_width_px}
          oneclick
          initialTool={tool ?? "draw"}
          hideUndo
        />
      ) : qaError ? (
        <QAError message={qaError} onRetry={onRetryQA} />
      ) : (
        <p className={styles.empty} aria-busy="true">
          載入線稿中…
        </p>
      )}
    </div>
  );
}

/** 線稿 QA 載入失敗：後端的原因＋「再試一次」（重抓 QA）。 */
function QAError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className={styles.qaError}>
      <p role="alert">{message}</p>
      <Button onClick={onRetry}>再試一次</Button>
    </div>
  );
}

// ---------- 修改列 ----------

const quiet = (p: Promise<unknown>) => p.catch(() => {});

function Row({
  title,
  tint,
  sub,
  actions,
  restore,
  children,
}: {
  title: string;
  tint: "pink" | "sky" | "yellow" | "green";
  sub: ReactNode;
  actions: ReactNode;
  restore: (() => void) | null;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <li aria-labelledby={id} className={styles.row}>
      <div className={styles.rowHead}>
        <span aria-hidden="true" className={`${styles.dot} ${styles[`dot_${tint}`]}`} />
        <div className={styles.rowText}>
          <h3 id={id} className={styles.rowTitle}>
            {title}
          </h3>
          <p className={styles.rowSub}>{sub}</p>
        </div>
        <div className={styles.rowActions}>{actions}</div>
      </div>
      {restore && (
        <button type="button" className={styles.restore} onClick={restore}>
          <span aria-hidden="true">↺</span> 還原成自動畫的那張
        </button>
      )}
      {children}
    </li>
  );
}

function useSavePage(bookId: number, pageId: number) {
  const toast = useToast();
  const patch = usePatchPageC(bookId, pageId);
  return (body: {
    scene_prompt?: string;
    action_prompt?: string;
    caption_zh?: string;
    caption_en?: string;
    caption_show_zh?: boolean;
    caption_show_en?: boolean;
  }) =>
    patch.mutateAsync(body).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });
}

function PoseRow({
  bookId,
  page,
  hasCharacter,
  active,
  flushEdits,
  restore,
  busy,
  textKey,
  trackSave,
}: {
  bookId: number;
  page: Page;
  hasCharacter: boolean;
  active: boolean;
  flushEdits: () => Promise<void>;
  restore: (() => void) | null;
  busy: boolean;
  textKey: number;
  trackSave: (p: Promise<unknown>) => Promise<unknown>;
}) {
  const id = useId();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const savePage = useSavePage(bookId, page.id);
  const action = usePageText(page.action_prompt, (v) => savePage({ action_prompt: v }), textKey);
  const regen = useRegenerate(bookId, page.id);
  const trimmed = action.value.trim();

  const again = async () => {
    try {
      await action.save();
    } catch {
      return; // 存檔失敗已提示；不要用舊的動作畫
    }
    await flushEdits();
    await regen
      .mutateAsync({ kind: "pose", action: trimmed })
      .catch((e: unknown) => toast.show(errMsg(e, "無法換一張角色動作"), "error"));
  };

  const sub = active ? "正在畫新的一張…" : trimmed ? `動作：${trimmed}` : "還沒寫動作";
  return (
    <Row
      title="角色動作"
      tint="pink"
      sub={sub}
      restore={restore}
      actions={
        <>
          <Button
            disabled={!hasCharacter || !trimmed || active || regen.isPending || busy}
            onClick={() => void again()}
          >
            換一張
          </Button>
          <Button aria-expanded={open} aria-controls={`${id}-panel`} onClick={() => setOpen(!open)}>
            改動作
          </Button>
        </>
      }
    >
      {!hasCharacter && <p className={styles.rowNote}>這本書還沒有主角，先到「主角與故事」設定。</p>}
      {open && (
        <div id={`${id}-panel`} className={styles.panel}>
          <label htmlFor={`${id}-action`} className={styles.panelLabel}>
            動作描述
          </label>
          <textarea
            id={`${id}-action`}
            rows={3}
            maxLength={MAX_ACTION}
            className={styles.textarea}
            value={action.value}
            aria-describedby={`${id}-hint`}
            onChange={(e) => action.setValue(e.target.value)}
            onBlur={() => quiet(trackSave(action.save()))}
          />
          <p id={`${id}-hint`} className={styles.rowNote}>
            {ACTION_HINT}
          </p>
          <ExclusiveChips
            label="表情"
            group={EXPRESSIONS}
            text={action.value}
            onChange={(v) => void quiet(trackSave(action.setNow(v)))}
          />
          <ExclusiveChips
            label="面向"
            group={FACINGS}
            text={action.value}
            onChange={(v) => void quiet(trackSave(action.setNow(v)))}
            hint={FACING_HINT}
          />
          <p className={styles.rowNote}>改好動作後按「換一張」。</p>
        </div>
      )}
    </Row>
  );
}

function BackgroundRow({
  bookId,
  page,
  bgGen,
  active,
  flushEdits,
  restore,
  busy,
  textKey,
  trackSave,
}: {
  bookId: number;
  page: Page;
  bgGen: Generation | undefined;
  active: boolean;
  flushEdits: () => Promise<void>;
  restore: (() => void) | null;
  busy: boolean;
  textKey: number;
  trackSave: (p: Promise<unknown>) => Promise<unknown>;
}) {
  const id = useId();
  const toast = useToast();
  const regen = useRegenerate(bookId, page.id);
  const upload = useUploadBackgroundC(bookId, page.id);
  const [uploading, setUploading] = useState(false);
  const [open, setOpen] = useState(false);
  const savePage = useSavePage(bookId, page.id);
  const scene = usePageText(page.scene_prompt, (v) => savePage({ scene_prompt: v }), textKey);
  const sceneText = scene.value.trim();

  const again = async () => {
    try {
      await scene.save(); // 剛改的場景先存好，新的背景才會用它
    } catch {
      return; // 存檔失敗已提示；不要用舊的場景畫
    }
    await flushEdits();
    await regen.mutateAsync({ kind: "background" }).catch((e: unknown) => toast.show(errMsg(e, "無法再畫一張背景"), "error"));
  };
  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同一個檔案再選一次也會觸發
    if (!file || uploading) return;
    setUploading(true);
    void (async () => {
      try {
        await flushEdits();
        await upload.mutateAsync(file);
      } catch (err) {
        toast.show(errMsg(err, "無法上傳背景圖"), "error");
      } finally {
        setUploading(false);
      }
    })();
  };

  const source = active
    ? "正在畫新的一張…"
    : uploading
      ? "上傳中…"
      : page.background_generation_id == null
        ? "還沒有背景"
        : bgGen?.source === "upload"
          ? "你上傳的圖片"
          : showRestore(page, "background")
            ? "你換過"
            : "AI 畫的";
  const uploaded = !active && !uploading && bgGen?.source === "upload";
  const sub = sceneText && !uploaded ? `${source}・場景：${sceneText}` : source;
  const uploadOff = uploading || busy;
  return (
    <Row
      title="背景"
      tint="sky"
      sub={sub}
      restore={restore}
      actions={
        <>
          <Button disabled={active || regen.isPending || busy} onClick={() => void again()}>
            AI 再畫一張
          </Button>
          <label className={styles.upload} data-disabled={uploadOff}>
            <span aria-hidden="true">↑</span> 上傳圖片
            <input type="file" accept={UPLOAD_ACCEPT} className={styles.file} disabled={uploadOff} onChange={onFile} />
          </label>
          <Button aria-expanded={open} aria-controls={`${id}-panel`} onClick={() => setOpen(!open)}>
            改場景
          </Button>
        </>
      }
    >
      {open && (
        <div id={`${id}-panel`} className={styles.panel}>
          <label htmlFor={`${id}-scene`} className={styles.panelLabel}>
            場景描述
          </label>
          <textarea
            id={`${id}-scene`}
            rows={3}
            maxLength={MAX_SCENE}
            className={styles.textarea}
            value={scene.value}
            onChange={(e) => scene.setValue(e.target.value)}
            onBlur={() => quiet(trackSave(scene.save()))}
          />
          <p className={styles.rowNote}>只寫地點和物品，不要寫人物或動物。改好場景後按「AI 再畫一張」。</p>
        </div>
      )}
    </Row>
  );
}

function LinesRow({
  bookId,
  page,
  qa,
  qaError,
  onRetryQA,
  blocked,
  tool,
  onTool,
  restore,
  busy,
}: {
  bookId: number;
  page: Page;
  qa: QA | undefined;
  qaError: string | null;
  onRetryQA: () => void;
  blocked: string | null;
  tool: StrokeOp | null;
  onTool: (t: StrokeOp) => void;
  restore: (() => void) | null;
  busy: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const autofix = useAutofix(bookId);
  const [fixing, setFixing] = useState(false);
  const gid = page.selected_generation_id;
  const noArt = gid == null;

  const runAutofix = async () => {
    if (gid == null || fixing) return;
    setFixing(true);
    try {
      const entry = peekEditsQueue(qc, page.id, gid, { oneclick: true });
      let added: number | null;
      if (entry?.autofix) {
        added = await entry.autofix(); // 畫布開著：補上的線要併進畫布（失敗已提示）
      } else {
        await entry?.flush().catch(() => {});
        const res = await autofix.mutateAsync({ pageId: page.id, generationId: gid, oneclick: true });
        const { autofix_added, ...rest } = res;
        qc.setQueryData<QA>(qk.qa(page.id), rest as QA);
        added = autofix_added;
      }
      if (added == null) return;
      toast.show(added > 0 ? `接起來了 ${added} 處` : "沒有可以自動接起來的線了", added > 0 ? "success" : "info");
      void refreshFix(qc, bookId, page.id);
    } catch (e) {
      toast.show(errMsg(e, "自動補線失敗"), "error");
    } finally {
      setFixing(false);
    }
  };

  const gaps = qa?.gap_count;
  const sub = noArt
    ? "還沒有線稿"
    : gaps == null
      ? "線稿"
      : `${gaps > 0 ? `${gaps} 處沒接起來（可以不管）` : "線都接好了"}${page.has_user_strokes ? "・有你畫的線" : ""}`;
  const penOff = noArt || blocked !== null || busy;
  return (
    <Row
      title="線條"
      tint="yellow"
      sub={sub}
      restore={restore}
      actions={
        <>
          <Button disabled={noArt || fixing || busy || blocked !== null} onClick={() => void runAutofix()}>
            {fixing ? "補線中…" : "自動補線"}
          </Button>
          <Button variant={tool === "draw" ? "sky" : "secondary"} aria-pressed={tool === "draw"} disabled={penOff} onClick={() => onTool("draw")}>
            畫筆
          </Button>
          <Button variant={tool === "erase" ? "sky" : "secondary"} aria-pressed={tool === "erase"} disabled={penOff} onClick={() => onTool("erase")}>
            擦除
          </Button>
        </>
      }
    >
      {blocked && <p className={styles.rowWarn}>{blocked}</p>}
      {!noArt && qaError && <QAError message={qaError} onRetry={onRetryQA} />}
    </Row>
  );
}

/**
 * 圖下方的字：「改字」打開中文／英文欄位；兩個開關（中文、英文，多選）決定這一頁印哪幾行，兩個都不選＝
 * 圖下方留一條空白讓小朋友自己寫；「把這個選擇套用到整本書」把這頁的開關套到每一頁。
 * busy（復原中、位置還在存、等著重新合成）時開關與套用停用；存的時候也停用，免得連按蓋來蓋去。
 */
function CaptionRow({
  bookId,
  page,
  textKey,
  busy,
  trackSave,
}: {
  bookId: number;
  page: Page;
  textKey: number;
  busy: boolean;
  trackSave: (p: Promise<unknown>) => Promise<unknown>;
}) {
  const id = useId();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const savePage = useSavePage(bookId, page.id);
  const applyAll = useCaptionDisplay(bookId, page.id);
  const [toggling, setToggling] = useState(false);
  const zh = usePageText(page.caption_zh ?? "", (v) =>
    captionError("zh", v) ? Promise.resolve() : savePage({ caption_zh: normalizeCaption(v) }),
    textKey,
  );
  const en = usePageText(page.caption_en ?? "", (v) =>
    captionError("en", v) ? Promise.resolve() : savePage({ caption_en: normalizeCaption(v) }),
    textKey,
  );
  // aria-disabled（不是 disabled）：存檔中按鈕仍保有鍵盤焦點；off 時 toggle/applyToBook 直接忽略。
  const off = busy || toggling || applyAll.isPending;

  const toggle = (field: "caption_show_zh" | "caption_show_en") => {
    if (off) return;
    setToggling(true);
    void quiet(trackSave(savePage({ [field]: !page[field] }))).finally(() => setToggling(false));
  };
  const applyToBook = () => {
    if (off) return;
    const choice = { show_zh: page.caption_show_zh, show_en: page.caption_show_en };
    applyAll.mutate(choice, {
      onSuccess: () => toast.show(`整本書都改成「${captionChoiceLabel(choice.show_zh, choice.show_en)}」了`, "success"),
      onError: (e) => toast.show(errMsg(e, "沒辦法套用到整本書"), "error"),
    });
  };

  const toggles: { field: "caption_show_zh" | "caption_show_en"; label: string }[] = [
    { field: "caption_show_zh", label: "中文" },
    { field: "caption_show_en", label: "英文" },
  ];
  return (
    <Row
      title="圖下方的字"
      tint="green"
      sub={captionSummary(page)}
      restore={null}
      actions={
        <Button aria-expanded={open} aria-controls={`${id}-panel`} onClick={() => setOpen(!open)}>
          改字
        </Button>
      }
    >
      <div className={styles.captionShow}>
        <div className={chip.presets} role="group" aria-label="要印哪幾行" aria-describedby={`${id}-show-hint`}>
          {toggles.map(({ field, label }) => {
            const on = page[field];
            return (
              <button
                key={field}
                type="button"
                className={chip.preset}
                data-added={on}
                aria-pressed={on}
                aria-disabled={off}
                onClick={() => toggle(field)}
              >
                {on ? <span aria-hidden="true">✓ </span> : null}
                {label}
              </button>
            );
          })}
        </div>
        <p id={`${id}-show-hint`} className={styles.rowNote}>
          兩個都不選＝圖下方留白，讓小朋友自己寫
        </p>
        <button type="button" className={styles.applyAll} aria-disabled={off} onClick={applyToBook}>
          把這個選擇套用到整本書
        </button>
      </div>
      {open && (
        <div id={`${id}-panel`} className={styles.panel}>
          <CaptionField kind="zh" value={zh.value} onChange={zh.setValue} onBlur={() => quiet(trackSave(zh.save()))} />
          <CaptionField kind="en" value={en.value} onChange={en.setValue} onBlur={() => quiet(trackSave(en.save()))} />
          <p className={styles.rowNote}>選填，印在圖的下方。離開欄位就會存。</p>
        </div>
      )}
    </Row>
  );
}
