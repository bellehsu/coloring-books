"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Book, type Page } from "@/api/client";
import { qk, useBook, useGenerations, usePages, useProduce, useProducePlan, useUpdateBook } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { errMsg } from "@/features/editor/errMsg";
import { PAPER_LABELS, PAPER_SIZES, PRINT_MODE_LABELS, ratioGroup, type PaperSize, type PrintMode } from "@/lib/layout";
import { applyImport } from "./applyImport";
import { CharacterSection } from "./CharacterSection";
import { StorySection } from "./StorySection";
import { buildPreview, pageMaterials, planCounts, planText, type DraftPage } from "./storyPreview";
import styles from "./SetupPage.module.css";

const MAX_TITLE = 100;
const MODES: PrintMode[] = ["home", "print_shop"];
const paperValue = (size: PaperSize, mode: PrintMode) => `${size}:${mode}`;

/** 網址是 /books/{id}/setup 就回傳 id（只在瀏覽器；SSR 回 null）。 */
function bookIdFromPath(): number | null {
  if (typeof window === "undefined") return null;
  const m = /^\/books\/(\d+)\/setup\/?$/.exec(window.location.pathname);
  return m ? Number(m[1]) : null;
}

/** 「開始做這本書」進行到哪一步（按鈕裡顯示）。 */
type Phase = { step: "import" } | { step: "upload"; done: number; of: number } | { step: "produce" };
const phaseLabel = (p: Phase) =>
  p.step === "import" ? "建立頁面中…" : p.step === "upload" ? `上傳背景 ${p.done}/${p.of}…` : "排進去中…";

/**
 * A 做一本新的著色書（spec §4.5、mockups/1_create.png）：① 書名和紙張 ② 主角 ③ 故事，底部「開始做這本書」。
 * - /books/new（bookId＝null）：① 填好（書名離開欄位、按 Enter，或第一次要用到書的動作）就建立書，網址換成
 *   /books/{id}/setup（history.replaceState：同一個元件繼續用，輸入到一半的東西都還在）。
 * - /books/{id}/setup：同一頁，給已經有的書（「主角與故事」）：列出現有頁，貼上的故事只填空欄位、補頁。
 * - 開始：匯入頁面 → 上傳選好的背景 → POST /produce → 到 B。429 也到 B（B 會顯示今天用完了）。
 */
export function SetupPage({ bookId: routeId }: { bookId: number | null }) {
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToast();
  // D1：Next 會把 /books/new 的 router tree 抄進 replaceState 換掉的歷史紀錄；之後按「上一頁」，
  // 網址是 /books/{id}/setup，畫出來的卻是這個元件（bookId＝null）。所以掛載時以網址為準（只在 client 讀，SSR 還是新書表單）。
  const [initialId] = useState(() => routeId ?? bookIdFromPath());
  const [bookId, setBookId] = useState(initialId);
  const has = bookId != null;
  const id = bookId ?? 0;
  const book = useBook(id, has);
  const pagesQ = usePages(id, has);
  const gensQ = useGenerations(id, {}, has);
  const plan = useProducePlan(id, has);
  const updateBook = useUpdateBook(id);
  const produce = useProduce(id);
  const paperId = useId();

  // ① 書名和紙張：草稿（null＝用伺服器的值）
  const [titleDraft, setTitleDraft] = useState<string | null>(null);
  const [paperDraft, setPaperDraft] = useState<{ size: PaperSize; mode: PrintMode } | null>(null);
  // ③ 故事
  const [text, setText] = useState("");
  /** 選了上傳背景的頁（PreviewEntry.key → 檔案）：按「開始做這本書」才上傳。 */
  const [uploads, setUploads] = useState<ReadonlyMap<string, File>>(new Map());
  const [drafts, setDrafts] = useState<DraftPage[]>([]);
  const [phase, setPhase] = useState<Phase | null>(null);

  const b = book.data;
  const title = titleDraft ?? b?.title ?? "";
  const trimmed = title.trim();
  const titleValid = trimmed.length >= 1 && trimmed.length <= MAX_TITLE;
  const size = paperDraft?.size ?? b?.paper_size ?? "a4";
  const mode = paperDraft?.mode ?? b?.print_mode ?? "home";

  // 標題：這次從 /books/new 開始，或這本書還沒設定過（沒有主角也沒有頁）→「做一本新的著色書」，否則「主角與故事」。
  // 第一次知道之後就固定，選了主角不會突然改標題。
  const [fresh, setFresh] = useState<boolean | null>(initialId == null ? true : null);
  if (fresh === null && b && pagesQ.data) setFresh(b.character_generation_id == null && pagesQ.data.length === 0);

  // ---------- 建立書 ----------
  const creating = useRef<Promise<number> | null>(null);
  function ensureBook(): Promise<number> {
    if (bookId != null) return Promise.resolve(bookId);
    if (!titleValid) return Promise.reject(new Error("先寫書名"));
    if (!creating.current) {
      const p = api
        .post<Book>("/books", { title: trimmed, paper_size: size, print_mode: mode })
        .then((made) => {
          qc.setQueryData(qk.book(made.id), made);
          void qc.invalidateQueries({ queryKey: qk.books() });
          // 不換頁面元件：輸入到一半的描述、故事、選好的圖片都留著；重新整理就是 /books/{id}/setup
          window.history.replaceState(null, "", `/books/${made.id}/setup`);
          // 草稿留著：建立途中又改的書名，下次離開欄位時再存
          setBookId(made.id);
          return made.id;
        });
      p.catch(() => {
        creating.current = null; // 失敗了下次可以再試
      });
      creating.current = p;
    }
    return creating.current;
  }
  const create = () =>
    ensureBook().catch((e: unknown) => {
      toast.show(errMsg(e, "沒辦法建立這本書，請再試一次"), "error");
      throw e;
    });

  function commitTitle() {
    if (!titleValid) return;
    if (!has) {
      void create().catch(() => {});
      return;
    }
    if (b && trimmed !== b.title) {
      updateBook.mutate(
        { title: trimmed },
        {
          onSuccess: () => setTitleDraft(null),
          onError: (e) => toast.show(errMsg(e, "書名沒有存起來，請再試一次"), "error"),
        },
      );
    }
  }

  function changePaper(value: string) {
    const [s, m] = value.split(":") as [PaperSize, PrintMode];
    setPaperDraft({ size: s, mode: m });
    if (!has) return;
    updateBook.mutate(
      { paper_size: s, print_mode: m },
      {
        onSuccess: () => setPaperDraft(null),
        onError: (e) => {
          setPaperDraft(null);
          toast.show(errMsg(e, "紙張沒有改成功"), "error");
        },
      },
    );
  }

  // ---------- 故事預覽與張數 ----------
  const pages = useMemo(
    () => [...(pagesQ.data ?? [])].sort((a, c) => a.position - c.position || a.id - c.id),
    [pagesQ.data],
  );
  const materials = useMemo(() => pageMaterials(pages, gensQ.data ?? []), [pages, gensQ.data]);
  const preview = useMemo(() => buildPreview(pages, text, materials, drafts), [pages, text, materials, drafts]);
  const uploadKeys = useMemo(() => new Set(uploads.keys()), [uploads]);
  const hasCharacter = b?.character_generation_id != null;
  const hasPages = preview.entries.length > 0;
  // 頁面重抓中也不能開始：不然照舊的頁面清單匯入，會重複建立頁面
  const canStart = hasCharacter && hasPages && (!has || (pagesQ.isSuccess && !pagesQ.isFetching));
  const counts = planCounts(preview.entries, uploadKeys, plan.data);
  const bar = planText(counts, canStart);
  const blockedHint = !hasCharacter ? "先選好主角" : !hasPages ? "先貼上故事或加一頁" : null;

  const setUpload = (key: string, file: File | null) =>
    setUploads((m) => {
      const next = new Map(m);
      if (file) next.set(key, file);
      else next.delete(key);
      return next;
    });

  const draftKey = useRef(0);
  const addDraft = () => setDrafts((d) => [...d, { key: ++draftKey.current, scene: "", action: "" }]);
  const changeDraft = (key: number, patch: Partial<Pick<DraftPage, "scene" | "action">>) =>
    setDrafts((d) => d.map((x) => (x.key === key ? { ...x, ...patch } : x)));
  const removeDraft = (key: number) => {
    setDrafts((d) => d.filter((x) => x.key !== key));
    setUpload(`d${key}`, null);
  };

  // 開始之後離開這頁（例如按「← 我的書櫃」）：伺服器的工作照做，但不要再把人拉到 B
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /** 頁面與生成紀錄跟伺服器對齊（上傳好的頁才會顯示「我上傳的圖片」、張數才對）。 */
  const refresh = (id: number) =>
    Promise.all([
      qc.invalidateQueries({ queryKey: qk.pages(id) }),
      qc.invalidateQueries({ queryKey: qk.generationsOf(id) }),
    ]).catch(() => {});

  // ---------- 開始做這本書 ----------
  async function start() {
    if (!canStart || bookId == null || phase) return;
    setPhase({ step: "import" });
    const { entries, rows } = preview;
    try {
      const ids = await applyImport(rows, pages, {
        createPage: (body) => api.post<Page>(`/books/${bookId}/pages`, body),
        updatePage: ({ pageId, ...body }) => api.patch<Page>(`/pages/${pageId}`, body),
      });
      // 「＋ 加一頁」的頁接在後面；建好就變成現有的頁（選好的上傳圖改用頁碼記）
      for (const e of entries) {
        if (e.draft == null) continue;
        const made = await api.post<Page>(`/books/${bookId}/pages`, { scene_prompt: e.scene, action_prompt: e.action });
        ids[e.n - 1] = made.id;
        const key = e.draft;
        setDrafts((d) => d.filter((x) => x.key !== key));
        setUploads((m) => {
          const file = m.get(e.key);
          if (!file) return m;
          const next = new Map(m);
          next.delete(e.key);
          next.set(`p${e.n}`, file);
          return next;
        });
      }
      await refresh(bookId);
      const chosen = entries.filter((e) => uploads.has(e.key) && ids[e.n - 1] != null);
      for (const [i, e] of chosen.entries()) {
        setPhase({ step: "upload", done: i + 1, of: chosen.length });
        try {
          await api.upload<Page>(`/pages/${ids[e.n - 1]}/backgrounds/upload`, uploads.get(e.key)!);
        } catch (err) {
          // 不排 /produce：不然這頁會被 AI 畫背景。改好再按一次（頁面已建立，不會重複）
          toast.show(`第 ${e.n} 頁的背景上傳失敗：${errMsg(err, "請換一張圖片再試")}`, "error");
          await refresh(bookId);
          setPhase(null);
          return;
        }
        await refresh(bookId); // 先讓畫面看到伺服器已經用這張圖，再拿掉選好的檔案（不會閃回「AI 畫背景」）
        setUpload(e.key, null); // 上傳好了：再按一次不會重傳
        setUpload(`p${e.n}`, null);
      }
      setPhase({ step: "produce" });
      try {
        await produce.mutateAsync();
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 429)) throw err;
        // 今天的次數用完：頁面都建好了，照樣到 B（B 顯示「今天的生成次數已用完」與「繼續做」）
        toast.show(err.message, "info");
      }
      void qc.invalidateQueries({ queryKey: qk.generationsOf(bookId) });
      if (mounted.current) router.push(`/books/${bookId}`);
    } catch (err) {
      toast.show(errMsg(err, "沒辦法開始做，請稍後再試"), "error");
      await refresh(bookId);
      setPhase(null);
    }
  }

  // ---------- 畫面 ----------
  if (has && !b) {
    return (
      <main className={styles.main}>
        {book.isError ? (
          <div className={styles.loadError}>
            <p role="alert">{errMsg(book.error, "無法載入繪本")}</p>
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

  const isNew = fresh !== false;
  const sameRatio = has && b ? ratioGroup(b.paper_size) : null;

  return (
    <main className={styles.main}>
      <header className={styles.head}>
        {isNew || !has ? (
          <Link href="/" className={styles.back}>
            <span aria-hidden="true">←</span> 我的書櫃
          </Link>
        ) : (
          <Link href={`/books/${bookId}`} className={styles.back}>
            <span aria-hidden="true">←</span> 整本書
          </Link>
        )}
        <h1 className={styles.h1}>{isNew ? "做一本新的著色書" : "主角與故事"}</h1>
      </header>

      <section aria-labelledby="setup-s1" className={styles.card}>
        <div className={styles.cardHead}>
          <span className={`${styles.num} ${styles.numPink}`} aria-hidden="true">
            1
          </span>
          <h2 id="setup-s1" className={styles.h2}>
            書名和紙張
          </h2>
        </div>
        <form
          className={styles.s1}
          onSubmit={(e) => {
            e.preventDefault();
            commitTitle();
          }}
        >
          <label className={styles.field}>
            <span className={styles.label}>書名</span>
            <input
              type="text"
              className={styles.input}
              value={title}
              maxLength={MAX_TITLE}
              autoComplete="off"
              placeholder="例如：小兔子的交通工具書"
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitTitle}
            />
          </label>
          <div className={styles.field}>
            {/* 不用包住 select 的 label：選單的選項文字會被算進名稱 */}
            <label htmlFor={paperId} className={styles.label}>
              紙張
            </label>
            <select
              id={paperId}
              className={styles.input}
              value={paperValue(size, mode)}
              onChange={(e) => changePaper(e.target.value)}
            >
              {PAPER_SIZES.map((s) =>
                MODES.map((m) => (
                  <option key={paperValue(s, m)} value={paperValue(s, m)} disabled={sameRatio != null && ratioGroup(s) !== sameRatio}>
                    {PAPER_LABELS[s]}・{PRINT_MODE_LABELS[m]}
                  </option>
                )),
              )}
            </select>
          </div>
          {has ? (
            <p className={styles.hint}>紙張之後只能改成同比例的尺寸。</p>
          ) : (
            <p className={styles.hint}>寫好書名就會建立這本書，接著選主角。</p>
          )}
        </form>
      </section>

      <CharacterSection bookId={bookId} book={b} titleValid={titleValid} ensureBook={create} />

      <StorySection
        preview={preview}
        text={text}
        onText={setText}
        uploads={uploads}
        onUpload={setUpload}
        onAddPage={addDraft}
        onDraftChange={changeDraft}
        onDraftRemove={removeDraft}
        existing={pages.length}
        disabled={phase != null}
      />

      <section aria-labelledby="setup-bar" className={styles.bar}>
        <div className={styles.barText} aria-live="polite">
          <h2 id="setup-bar" className={styles.barHeading}>
            {bar.heading}
          </h2>
          {bar.detail && <p className={styles.barDetail}>{bar.detail}</p>}
          {bar.defer && <p className={styles.barDefer}>{bar.defer}</p>}
          {blockedHint && <p className={styles.barDetail}>{blockedHint}，就可以開始。</p>}
        </div>
        <Button
          variant="primary"
          size="lg"
          className={styles.start}
          disabled={!canStart || phase != null}
          aria-busy={phase != null}
          onClick={() => void start()}
        >
          {phase ? phaseLabel(phase) : "開始做這本書"}
        </Button>
      </section>
    </main>
  );
}
