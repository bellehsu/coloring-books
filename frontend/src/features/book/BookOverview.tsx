"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import type { Book, BookOverview as Overview, OverviewPage } from "@/api/client";
import {
  overviewActive,
  qk,
  useBook,
  useBookOverview,
  useProduce,
  useProducePlan,
  useRedrawPoses,
} from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editing/errMsg";
import { fixHref, printedCaptions } from "@/features/fix/fixState";
import { formatRemaining } from "@/lib/eta";
import { NO_QUOTA } from "@/lib/limits";
import { ExportSheet } from "./ExportSheet";
import { RedrawBar, type RedrawCount } from "./RedrawBar";
import {
  FILTERS,
  STATE_LABEL,
  cardMode,
  countPages,
  countsLine,
  filterPages,
  inAttention,
  progressPages,
  softNote,
  type Filter,
} from "./overviewState";
import styles from "./BookOverview.module.css";

const DONE_TITLE = "✓ 做好了";

const todoCount = (o: Overview) => o.pages.filter((p) => p.state === "todo").length;

/** 這一輪做完時的 toast：整本都好了，或還有幾頁要重畫／明天繼續／還沒做完。 */
function finishedMessage(o: Overview): string {
  const mode = cardMode(o);
  if (mode === "redraw") return `還有 ${o.redraw_count} 頁要重畫`;
  if (mode === "deferred") return `今天的做好了，還有 ${o.deferred_count} 頁明天繼續`;
  if (mode === "unfinished") return `做好了，還有 ${todoCount(o)} 頁還沒做完`;
  return "整本做好了！";
}

/** B 整本書（spec §4.3）：狀態卡、篩選、頁面卡、「印出這本書」。 */
export function BookOverview({ bookId }: { bookId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const book = useBook(bookId);
  const overview = useBookOverview(bookId, { background: true });
  const plan = useProducePlan(bookId);
  const produceM = useProduce(bookId);
  const [filter, setFilter] = useState<Filter>("all");
  const [exporting, setExporting] = useState(false);
  // 選取多頁：勾要重畫主角的頁，底部的重畫列一次排
  const redrawM = useRedrawPoses(bookId);
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set());
  const [redrawCount, setRedrawCount] = useState<RedrawCount>(1);

  const o = overview.data;
  const active = o ? overviewActive(o) : false;
  const title = book.data?.title;

  // 做完了：看過「還在做」（或剛按了開始／繼續做而且有排到東西），之後變成沒在做 → toast＋分頁標題。
  const wasActive = useRef(false);
  const consumedRun = useRef(0);
  const originalTitle = useRef<string | null>(null);
  useEffect(() => {
    originalTitle.current = document.title;
    return () => {
      if (originalTitle.current !== null) document.title = originalTitle.current;
    };
  }, []);
  const produced = produceM.data;
  const submittedAt = produceM.submittedAt;
  const producePending = produceM.isPending;
  useEffect(() => {
    if (!o || producePending) return;
    if (produced && submittedAt !== consumedRun.current) {
      consumedRun.current = submittedAt;
      if (produced.queued.length + produced.pipeline_pages.length > 0) wasActive.current = true;
    }
    if (active) {
      // 又開始做了：拿掉上一輪的「✓ 做好了」
      if (document.title.startsWith(DONE_TITLE) && originalTitle.current !== null) document.title = originalTitle.current;
      wasActive.current = true;
      return;
    }
    if (!wasActive.current) return;
    wasActive.current = false;
    toast.show(finishedMessage(o), "success");
    // 還有頁面要重畫（主角與故事改過）：還不算做好，分頁標題不加「✓ 做好了」
    if (cardMode(o) !== "redraw") document.title = title ? `${DONE_TITLE}｜${title}` : DONE_TITLE;
    void qc.invalidateQueries({ queryKey: qk.producePlan(bookId) });
    void qc.invalidateQueries({ queryKey: qk.pages(bookId) });
    void qc.invalidateQueries({ queryKey: qk.book(bookId) });
    void qc.invalidateQueries({ queryKey: qk.books() });
    void qc.invalidateQueries({ queryKey: qk.exportSummary(bookId) });
  }, [o, active, producePending, produced, submittedAt, title, toast, qc, bookId]);

  async function produce() {
    try {
      const r = await produceM.mutateAsync();
      if (r.queued.length + r.pipeline_pages.length === 0 && r.deferred_pages.length === 0)
        toast.show("沒有可以繼續做的頁面，先到「主角與故事」把故事寫好", "info");
    } catch (e) {
      toast.show(errMsg(e, "沒辦法開始做，請稍後再試"), "error");
    }
  }

  function leaveSelect() {
    setSelecting(false);
    setPicked(new Set());
    setRedrawCount(1);
  }

  function togglePick(id: number) {
    setPicked((cur) => {
      const nextSet = new Set(cur);
      if (!nextSet.delete(id)) nextSet.add(id);
      return nextSet;
    });
  }

  async function redraw(pages: OverviewPage[]) {
    const pageIds = [...pages].sort((x, y) => x.position - y.position).filter((p) => picked.has(p.id)).map((p) => p.id);
    try {
      const r = await redrawM.mutateAsync({ pageIds, count: redrawCount });
      leaveSelect();
      if (r.skipped.length > 0) toast.show(`${r.skipped.length} 頁沒有動作描述，沒有重畫`, "info");
    } catch (e) {
      toast.show(errMsg(e, "沒辦法重畫，請稍後再試"), "error");
    }
  }

  const b = book.data;
  if (!b || !o) {
    const err = book.error ?? overview.error;
    return (
      <main className={styles.main}>
        {err ? (
          <div className={styles.loadError}>
            <p role="alert">{errMsg(err, "無法載入繪本")}</p>
            <Link href="/">回到我的書櫃</Link>
          </div>
        ) : (
          <p className={styles.loadError} aria-busy="true">
            載入中…
          </p>
        )}
      </main>
    );
  }

  const counts = countPages(o.pages);
  const shown = filterPages(o.pages, filter);
  const noQuota = plan.data?.remaining === 0;
  const pickedCount = o.pages.filter((p) => picked.has(p.id)).length;

  return (
    <main className={`${styles.main} ${selecting ? styles.mainSelecting : ""}`}>
      <header className={styles.head}>
        <Link href="/" className={styles.back} aria-label="我的書櫃">
          <span aria-hidden="true">←</span>
          <span className={styles.backText} aria-hidden="true">
            我的書櫃
          </span>
        </Link>
        <h1 className={styles.h1}>{b.title}</h1>
        <nav aria-label="這本書" className={styles.links}>
          <Link href={`/books/${bookId}/setup`} className={styles.link}>
            主角與故事
          </Link>
          <Link href={`/books/${bookId}/editor`} className={styles.link}>
            進階編輯
          </Link>
        </nav>
        <button
          type="button"
          aria-pressed={selecting}
          className={styles.selectToggle}
          onClick={() => (selecting ? leaveSelect() : setSelecting(true))}
        >
          {selecting ? "✓ 選取多頁" : "選取多頁"}
        </button>
        <div className={styles.printBar}>
          <Button variant="primary" size="lg" className={styles.print} onClick={() => setExporting(true)}>
            印出這本書
          </Button>
        </div>
      </header>

      <StatusCard
        book={b}
        overview={o}
        noQuota={noQuota}
        busy={produceM.isPending}
        onProduce={() => void produce()}
      />

      <div role="group" aria-label="篩選頁面" className={styles.filters}>
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            aria-pressed={filter === f.value}
            className={styles.filter}
            onClick={() => setFilter(f.value)}
          >
            {f.label} {counts[f.value]}
          </button>
        ))}
        {selecting && <span className={styles.selectHint}>點卡片勾選要重畫主角的頁</span>}
      </div>

      {shown.length === 0 ? (
        <p className={styles.empty}>{o.pages.length === 0 ? "還沒有頁面，先到「主角與故事」寫故事。" : "這裡沒有頁面。"}</p>
      ) : (
        <ul aria-label="頁面" className={styles.grid}>
          {shown.map((p) => (
            <PageCard
              key={p.id}
              bookId={bookId}
              page={p}
              aspect={b.layout.content_px}
              attention={filter === "attention"}
              pick={selecting ? { on: picked.has(p.id), toggle: () => togglePick(p.id) } : null}
            />
          ))}
        </ul>
      )}

      {selecting && (
        <RedrawBar
          selected={pickedCount}
          count={redrawCount}
          onCount={setRedrawCount}
          onSubmit={() => void redraw(o.pages)}
          onCancel={leaveSelect}
          busy={redrawM.isPending}
        />
      )}

      {exporting && <ExportSheet book={b} pages={o.pages} onClose={() => setExporting(false)} />}
    </main>
  );
}

interface StatusCardProps {
  book: Book;
  overview: Overview;
  noQuota: boolean;
  busy: boolean;
  onProduce: () => void;
}

function StatusCard({ book, overview: o, noQuota, busy, onProduce }: StatusCardProps) {
  const headingId = useId();
  const mode = cardMode(o);
  const todo = todoCount(o);
  const firstAttention = o.pages.find(inAttention);
  const canStart = book.character_generation_id != null && o.pages.length > 0;

  const heading =
    mode === "running"
      ? "正在做這本書…"
      : mode === "redraw"
        ? `還有 ${o.redraw_count} 頁要重畫`
        : mode === "notStarted"
          ? "還沒開始做"
          : mode === "deferred"
            ? `還有 ${o.deferred_count} 頁明天繼續`
            : mode === "unfinished"
              ? `還有 ${todo} 頁還沒做完`
              : "整本做好了！";

  const produceButton = (label: string) => (
    <Button variant="primary" size="lg" disabled={busy || noQuota} onClick={onProduce} className={styles.action}>
      {busy ? "排進去中…" : label}
    </Button>
  );

  let action: ReactNode = null;
  if (mode === "notStarted") {
    action = canStart ? (
      produceButton("開始做這本書")
    ) : (
      <Link href={`/books/${book.id}/setup`} className={`${styles.pillLink} ${styles.pillPrimary} ${styles.action}`}>
        開始做這本書
      </Link>
    );
  } else if (mode === "redraw" || mode === "deferred" || mode === "unfinished") {
    action = produceButton("繼續做");
  }
  const lookLink =
    mode !== "running" && mode !== "notStarted" && firstAttention ? (
      <Link href={fixHref(book.id, firstAttention.id, "attention")} className={`${styles.pillLink} ${styles.pillSky} ${styles.action}`}>
        去看那一頁 →
      </Link>
    ) : null;

  const { ready, total } = progressPages(o);
  const pagesLine = `做好 ${ready} / ${total} 頁`;
  const showQuotaNote =
    noQuota && (mode === "redraw" || mode === "deferred" || mode === "unfinished" || (mode === "notStarted" && canStart));

  return (
    <section aria-labelledby={headingId} className={styles.status}>
      {book.character_generation_id != null && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`/api/generations/${book.character_generation_id}/image`} alt="" className={styles.avatar} />
      )}
      <div className={styles.statusBody}>
        <h2 id={headingId} className={styles.h2}>
          {heading}
        </h2>
        {mode === "running" ? (
          <>
            <div
              role="progressbar"
              aria-label="整本的進度"
              aria-valuemin={0}
              aria-valuemax={total}
              aria-valuenow={ready}
              className={styles.progress}
            >
              <div className={styles.progressFill} style={{ width: `${total ? (ready / total) * 100 : 0}%` }} />
            </div>
            <p className={styles.counts}>
              {pagesLine}
              {o.estimate_s > 0 && `・${formatRemaining(o.estimate_s)}`}
            </p>
            <p className={styles.counts}>可以先去忙，好了會通知你</p>
          </>
        ) : (
          <>
            {o.pages.length > 0 && (
              <ul aria-label="每一頁的狀態" className={styles.segbar}>
                {o.pages.map((p) => (
                  <li key={p.id} className={`${styles.seg} ${styles[`seg_${p.state}`]}`}>
                    <span className={styles.srOnly}>
                      第 {p.position + 1} 頁：{STATE_LABEL[p.state]}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {/* 沒有頁面：不說「做好 0 / 0 頁」，只說「還沒有頁面」 */}
            {o.pages.length > 0 && <p className={styles.counts}>{pagesLine}</p>}
            {ready !== total || o.pages.length === 0 ? <p className={styles.counts}>{countsLine(o.pages)}</p> : null}
            {mode === "notStarted" && !canStart && (
              <p className={styles.counts}>先設定主角、寫好故事，就可以開始做。</p>
            )}
            {showQuotaNote && <p className={styles.quota}>{NO_QUOTA}</p>}
          </>
        )}
      </div>
      {(action || lookLink) && (
        <div className={styles.actions}>
          {action}
          {lookLink}
        </div>
      )}
    </section>
  );
}

const TINTS = ["tintPink", "tintSky", "tintYellow", "tintGreen", "tintPurple"] as const;

function PageCard({
  bookId,
  page: p,
  aspect,
  attention,
  pick,
}: {
  bookId: number;
  page: OverviewPage;
  aspect: number[];
  /** 從「請看一下」篩選打開：C 的上一頁／下一頁只在要看的頁之間跳。 */
  attention: boolean;
  /** 選取多頁：卡片右上角的勾選框，點卡片是勾／取消勾（不打開 C）。 */
  pick: { on: boolean; toggle: () => void } | null;
}) {
  const n = p.position + 1;
  const src = p.lineart_url ?? p.thumb_url;
  const note = softNote(p);
  // 批次重畫後換了新動作（review）：狀態還是可以印，但卡片顯示軟性的「請看一下」（製作中時照樣顯示製作中）
  const pillState = p.review && p.state === "ready" ? "attention" : p.state;
  const [cw, ch] = aspect;
  // 卡片下方的字＝這頁真的會印的字（中文優先）；兩個都不印時說圖下方留白
  const printed = printedCaptions(p);
  const cardClass = `${styles.card} ${p.state === "attention" ? styles.cardAttention : ""}`;
  const content = (
    <>
      <div className={styles.frame}>
        <span className={`${styles.no} ${styles[TINTS[p.position % TINTS.length]]}`}>{n}</span>
        {pick && (
          <input
            type="checkbox"
            aria-label={`選第 ${n} 頁`}
            checked={pick.on}
            onChange={pick.toggle}
            className={styles.pick}
          />
        )}
        <div className={styles.art} style={{ aspectRatio: `${cw} / ${ch}` }}>
          {src ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={src} alt={`第 ${n} 頁`} loading="lazy" className={styles.img} />
          ) : (
            <span className={styles.noArt}>{p.state === "working" ? "製作中…" : "還沒有圖"}</span>
          )}
        </div>
        {printed === "blank" ? (
          <span className={styles.captionBlank}>圖下方留白</span>
        ) : (
          (printed.zh || printed.en) && <b className={styles.caption}>{printed.zh || printed.en}</b>
        )}
      </div>
      <span className={`${styles.pill} ${styles[`pill_${pillState}`]}`}>{STATE_LABEL[pillState]}</span>
      {note && <span className={p.state === "attention" ? styles.noteWarn : styles.note}>{note}</span>}
    </>
  );
  return (
    <li className={styles.cell}>
      {pick ? (
        // 整張卡是勾選框的 label：點哪裡都是勾／取消勾
        <label className={`${cardClass} ${styles.cardPick} ${pick.on ? styles.cardPicked : ""}`}>{content}</label>
      ) : (
        <Link href={fixHref(bookId, p.id, attention ? "attention" : null)} className={cardClass}>
          {content}
        </Link>
      )}
    </li>
  );
}
