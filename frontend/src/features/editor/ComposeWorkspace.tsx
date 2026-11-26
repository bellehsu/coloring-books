"use client";

import { useId, useRef, useState, type ChangeEvent } from "react";
import Link from "next/link";
import type { Generation, Page } from "@/api/client";
import {
  isActive, useBook, useCleanupBackground, useGenerateBackground, useGeneratePose, useSetCharacter, useUpdatePage, useUploadBackground,
} from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { CaptionField, captionError } from "@/components/ui/CaptionField";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import {
  ACTION_HINT, ACTIONS, appendPhrase, EXPRESSIONS, FACING_HINT, FACINGS, hasPhrase,
} from "@/lib/actions";
import { CLEANUP_LABEL, CLEANUP_NOT_PORTRAIT, isPortrait } from "@/lib/cleanup";
import { MAX_ACTION, MAX_SCENE, normalizeCaption } from "@/lib/limits";
import { quiet } from "@/lib/quiet";
import { UPLOAD_ACCEPT, UPLOAD_HINT } from "@/lib/upload";
import { useSingleFlight } from "@/lib/useSingleFlight";
import { errMsg } from "@/features/editing/errMsg";
import { CharacterToggle, type CharacterBody } from "@/features/editing/CharacterToggle";
import { thumbNumber, Thumbs, useThumbList } from "./Thumbs";
import { ExclusiveChips } from "@/features/editing/ExclusiveChips";
import chips from "@/features/editing/chips.module.css";
import { usePageText } from "@/features/editing/usePageText";
import panel from "./Inspector.module.css";
import styles from "./ComposeWorkspace.module.css";


export interface ComposeWorkspaceProps {
  bookId: number;
  /** 呼叫端用 key={page.id}：改到一半的場景／動作文字只屬於這一頁。 */
  page: Page;
  /** 頁面選用的背景與角色動作（找不到或還沒選時 undefined）；卡片標題旁說明是哪一張。 */
  background: Generation | undefined;
  pose: Generation | undefined;
  /** 點縮圖選用；呼叫端負責先等修線寫完再 useSelectBackground／useSelectPose。 */
  onPickBackground: (g: Generation) => void;
  onPickPose: (g: Generation) => void;
  /** 選用請求進行中（或正在等修線寫完）：縮圖與「上傳背景圖」暫時不能用。 */
  picking?: boolean;
  /**
   * 上傳背景前呼叫並等它完成：上傳會換掉背景（並清掉合成圖），呼叫端先讓目前選圖的修線寫完，
   * 否則晚到的修線 PUT 會被後端拒絕。和點縮圖選用走同一個前置步驟。
   */
  beforeReplaceBackground?: () => Promise<unknown>;
  /** 上傳的背景已成為這頁的背景（回傳更新後的頁）：EditorPage 用來排自動合成。 */
  onBackgroundUploaded?: (page: Page) => void;
  /** 手機寬度（§4.5）：兩張卡片先收成並排的精簡卡片（縮圖＋標題），點了才展開。 */
  compact?: boolean;
}

/**
 * 「① 構圖」的右欄：背景卡片與角色動作卡片。
 * 背景：縮圖（單選）、「換一張」（Qwen，用本頁場景文字）、「上傳背景圖」、「清掉框外雜物」（肖像頁）、收合的場景文字編輯。
 * 角色動作：縮圖、「換一張」（用本頁動作文字；頁面沒寫就用最新一張的動作）、收合的動作文字編輯與常用動作。
 * 改過的文字在離開欄位或按「換一張」時存回頁面（PATCH），存好才產生。
 * compact（手機）：收合時只顯示精簡卡片，展開後是完整卡片加「收合」按鈕；收合不會丟掉改到一半的文字。
 */
export function ComposeWorkspace(props: ComposeWorkspaceProps) {
  return (
    <div className={styles.cards} data-compact={!!props.compact}>
      <BackgroundCard {...props} />
      <PoseCard {...props} />
      <CaptionCard bookId={props.bookId} page={props.page} />
    </div>
  );
}

/**
 * 手機的收合狀態：只有 compact 時才有作用。展開／收合會換掉按下的那顆按鈕，
 * 焦點移到換上來的按鈕（「收合」或精簡卡片），鍵盤使用者不會掉回頁首。
 */
function useCollapse(compact: boolean | undefined) {
  const [open, setOpen] = useState(false);
  const moveFocus = useRef(false);
  const toggle = (next: boolean) => {
    moveFocus.current = true;
    setOpen(next);
  };
  const focusTarget = (el: HTMLButtonElement | null) => {
    if (el && moveFocus.current) {
      moveFocus.current = false;
      el.focus();
    }
  };
  return { collapsed: !!compact && !open, expand: () => toggle(true), collapse: () => toggle(false), focusTarget };
}

/** 收合時的精簡卡片：目前選用的縮圖＋標題＋能做什麼，點了展開。 */
function CompactCard({
  title,
  sub,
  imageUrl,
  contain,
  onExpand,
  buttonRef,
}: {
  title: string;
  sub: string;
  imageUrl: string | null | undefined;
  contain?: boolean;
  onExpand: () => void;
  buttonRef: (el: HTMLButtonElement | null) => void;
}) {
  return (
    <button ref={buttonRef} type="button" className={styles.compact} aria-expanded={false} onClick={onExpand}>
      <span className={styles.compactThumb} data-contain={!!contain}>
        {imageUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imageUrl} alt="" className={styles.compactImg} />
        )}
      </span>
      <span className={styles.compactText}>
        <span className={styles.title}>{title} </span>
        <span className={styles.compactSub}>{sub}</span>
      </span>
    </button>
  );
}

/** 展開的卡片右上角（只在 compact）：收回精簡卡片。 */
function CollapseButton({
  title,
  onCollapse,
  buttonRef,
}: {
  title: string;
  onCollapse: () => void;
  buttonRef: (el: HTMLButtonElement | null) => void;
}) {
  return (
    <button ref={buttonRef} type="button" className={styles.collapse} aria-expanded={true} aria-label={`收合${title}`} onClick={onCollapse}>
      <Icons.chevron size={16} className={styles.collapseChev} />
    </button>
  );
}

function useSavePage(bookId: number, pageId: number) {
  const toast = useToast();
  const update = useUpdatePage(bookId);
  return (body: { scene_prompt?: string; action_prompt?: string; caption_zh?: string; caption_en?: string }) =>
    update.mutateAsync({ pageId, ...body }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });
}

const CAPTION_OFF = "這行目前不印（在處理頁面可打開）";

/**
 * 圖下方的文字（中文字、英文字）：和場景、動作欄位一樣，離開欄位時存回頁面。
 * 超過上限（以 code point 計）時不存，欄位下方顯示訊息。手機也直接顯示（欄位很小，不收合）。
 * C 關掉的那行（caption_show_*，沒有欄位＝印）照樣能改，但說明目前不印；兩行都關就不說「印在圖下方」。
 */
function CaptionCard({ bookId, page }: { bookId: number; page: Page }) {
  const id = useId();
  const savePage = useSavePage(bookId, page.id);
  const zh = usePageText(page.caption_zh ?? "", (v) => (captionError("zh", v) ? Promise.resolve() : savePage({ caption_zh: normalizeCaption(v) })));
  const en = usePageText(page.caption_en ?? "", (v) => (captionError("en", v) ? Promise.resolve() : savePage({ caption_en: normalizeCaption(v) })));
  const zhOn = page.caption_show_zh !== false;
  const enOn = page.caption_show_en !== false;
  return (
    <section aria-labelledby={`${id}-h`} className={styles.card}>
      <div className={styles.head}>
        <h2 id={`${id}-h`} className={styles.title}>
          圖下方的文字
        </h2>
      </div>
      <div className={styles.captionFields}>
        <div className={styles.captionLine}>
          <CaptionField kind="zh" value={zh.value} onChange={zh.setValue} onBlur={() => quiet(zh.save())} />
          {!zhOn && <p className={styles.hint}>{CAPTION_OFF}</p>}
        </div>
        <div className={styles.captionLine}>
          <CaptionField kind="en" value={en.value} onChange={en.setValue} onBlur={() => quiet(en.save())} />
          {!enOn && <p className={styles.hint}>{CAPTION_OFF}</p>}
        </div>
      </div>
      {(zhOn || enOn) && <p className={styles.hint}>選填，印在列印頁的圖下方。</p>}
    </section>
  );
}

function BackgroundCard({
  bookId,
  page,
  background,
  onPickBackground,
  picking,
  beforeReplaceBackground,
  onBackgroundUploaded,
  compact,
}: ComposeWorkspaceProps) {
  const id = useId();
  const fold = useCollapse(compact);
  const toast = useToast();
  const savePage = useSavePage(bookId, page.id);
  const scene = usePageText(page.scene_prompt, (v) => savePage({ scene_prompt: v }));
  const generate = useGenerateBackground(bookId);
  const upload = useUploadBackground(bookId);
  const flight = useSingleFlight();
  const uploading = useSingleFlight();
  const cleanup = useCleanupBackground(bookId);
  const cleaning = useSingleFlight();
  const { list } = useThumbList(bookId, "background", page.id);

  const n = thumbNumber(list, background?.id);
  const info = !background
    ? "還沒選"
    : background.source === "upload"
      ? "上傳的圖片"
      : n != null
        ? `AI 產生 · 第 ${n} 張`
        : "AI 產生";

  const regenerate = () =>
    flight.run(async () => {
      try {
        await scene.save();
      } catch {
        return; // 存檔失敗已提示；不要用舊的場景產生
      }
      await generate
        .mutateAsync({ pageId: page.id })
        .catch((e: unknown) => toast.show(errMsg(e, "無法產生背景"), "error"));
    });

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 同一個檔案再選一次也會觸發
    if (!file) return;
    // 先等修線寫完再上傳；整段期間停用（ref 擋住同一輪 render 前的第二次選檔）
    void uploading.run(async () => {
      await beforeReplaceBackground?.();
      await upload.mutateAsync({ pageId: page.id, file }).then(
        (updated) => onBackgroundUploaded?.(updated),
        (err: unknown) => toast.show(errMsg(err, "無法上傳背景圖"), "error"),
      );
    });
  };
  const uploadBusy = uploading.busy || upload.isPending;
  const uploadDisabled = uploadBusy || !!picking;

  // 清掉框外雜物：要有選用的背景、這頁沒有背景在產生（或清理）、而且是肖像模式
  const portrait = isPortrait(page);
  const bgBusy = list?.some(isActive) ?? false;
  const cleanDisabled =
    page.background_generation_id == null || bgBusy || !portrait || cleaning.busy || cleanup.isPending || uploadBusy || !!picking;
  const clean = () =>
    cleaning.run(() =>
      cleanup.mutateAsync({ pageId: page.id }).then(
        () => undefined,
        (e: unknown) => toast.show(errMsg(e, "無法清掉框外雜物"), "error"),
      ),
    );

  if (fold.collapsed) {
    return (
      <CompactCard
        title="背景"
        sub={uploadBusy ? "上傳中…" : "換一張・上傳"}
        imageUrl={background?.image_url}
        onExpand={fold.expand}
        buttonRef={fold.focusTarget}
      />
    );
  }
  return (
    <section aria-labelledby={`${id}-h`} className={styles.card}>
      <div className={styles.head}>
        <h2 id={`${id}-h`} className={styles.title}>
          背景
        </h2>
        <span className={styles.info}>{info}</span>
        {compact && <CollapseButton title="背景" onCollapse={fold.collapse} buttonRef={fold.focusTarget} />}
      </div>
      <Thumbs
        bookId={bookId}
        kind="background"
        pageId={page.id}
        selectedId={page.background_generation_id}
        onPick={onPickBackground}
        picking={picking}
      />
      <div className={styles.actions}>
        <Button aria-label="換一張背景" disabled={flight.busy || generate.isPending} onClick={() => void regenerate()}>
          換一張
        </Button>
        <label className={styles.upload} data-busy={uploadBusy} data-disabled={uploadDisabled}>
          <Icons.upload size={18} />
          {uploadBusy ? "上傳中…" : "上傳背景圖"}
          <input
            type="file"
            accept={UPLOAD_ACCEPT}
            className={styles.file}
            disabled={uploadDisabled}
            aria-describedby={`${id}-hint`}
            onChange={onFile}
          />
        </label>
        <Button disabled={cleanDisabled} aria-describedby={portrait ? undefined : `${id}-clean`} onClick={() => void clean()}>
          {CLEANUP_LABEL}
        </Button>
      </div>
      {!portrait && (
        <p id={`${id}-clean`} className={styles.hint}>
          {CLEANUP_NOT_PORTRAIT}
        </p>
      )}
      <p id={`${id}-hint`} className={styles.hint}>
        {UPLOAD_HINT}
      </p>
      <details className={styles.details}>
        <summary className={styles.summary}>
          <Icons.chevron size={14} className={styles.chev} />
          <span className={styles.summaryText}>
            場景：{scene.value.trim() || "（還沒寫）"}
          </span>
        </summary>
        <div className={styles.editor}>
          <div className={panel.field}>
            <label htmlFor={`${id}-scene`}>場景描述</label>
            <textarea
              id={`${id}-scene`}
              rows={3}
              maxLength={MAX_SCENE}
              className={panel.textarea}
              value={scene.value}
              aria-describedby={`${id}-scene-count`}
              onChange={(e) => scene.setValue(e.target.value)}
              onBlur={() => quiet(scene.save())}
            />
            <span id={`${id}-scene-count`} className={panel.counter}>
              {scene.value.length} / {MAX_SCENE}
            </span>
          </div>
          <p className={styles.hint}>可以在這裡改場景文字，再按「換一張」。</p>
        </div>
      </details>
    </section>
  );
}

function PoseCard({ bookId, page, background, pose, onPickPose, picking, compact }: ComposeWorkspaceProps) {
  const id = useId();
  const fold = useCollapse(compact);
  const toast = useToast();
  const book = useBook(bookId).data;
  const hasCharacter = book?.character_generation_id != null;
  const savePage = useSavePage(bookId, page.id);
  const { list } = useThumbList(bookId, "pose", page.id);
  // 頁面沒寫動作（舊頁面）時，沿用最新一張角色動作的描述
  const latestText = list?.length ? list[list.length - 1].user_text : "";
  const action = usePageText(page.action_prompt || latestText, (v) => savePage({ action_prompt: v }));
  const generate = useGeneratePose(bookId);
  const flight = useSingleFlight();
  const trimmed = action.value.trim();
  const setChar = useSetCharacter(bookId);
  const toggleCharacter = (body: CharacterBody) =>
    setChar.mutateAsync({ pageId: page.id, body }).then(
      () => {},
      (e: unknown) => toast.show(errMsg(e, "無法切換主角"), "error"),
    );
  const include = page.include_character !== false;

  const n = thumbNumber(list, pose?.id);
  const info = !pose ? "還沒選" : n != null ? `第 ${n} 張` : "";

  const regenerate = () =>
    flight.run(async () => {
      if (!trimmed) return;
      try {
        await action.save();
      } catch {
        return;
      }
      await generate
        .mutateAsync({ pageId: page.id, action: trimmed })
        .catch((e: unknown) => toast.show(errMsg(e, "無法產生角色動作"), "error"));
    });

  if (fold.collapsed) {
    return (
      <CompactCard
        title="角色動作"
        sub="換一張"
        imageUrl={pose?.cutout_url ?? pose?.image_url}
        contain
        onExpand={fold.expand}
        buttonRef={fold.focusTarget}
      />
    );
  }
  return (
    <section aria-labelledby={`${id}-h`} className={styles.card}>
      <div className={styles.head}>
        <h2 id={`${id}-h`} className={styles.title}>
          角色動作
        </h2>
        <span className={styles.info}>{info}</span>
        {compact && <CollapseButton title="角色動作" onCollapse={fold.collapse} buttonRef={fold.focusTarget} />}
      </div>
      <CharacterToggle
        page={page}
        backgroundUploaded={background?.source === "upload"}
        busy={setChar.isPending || !!picking}
        onToggle={toggleCharacter}
      />
      {!include && <p className={styles.hint}>這頁不放主角</p>}
      {include && <Thumbs bookId={bookId} kind="pose" pageId={page.id} selectedId={page.pose_generation_id} onPick={onPickPose} picking={picking} />}
      {include && <Button
        aria-label="換一張角色動作"
        disabled={!hasCharacter || !trimmed || flight.busy || generate.isPending}
        onClick={() => void regenerate()}
      >
        換一張
      </Button>}
      {include && book && !hasCharacter && (
        <span className={styles.hint}>
          <Link href={`/books/${bookId}/setup`}>先設定角色</Link>
        </span>
      )}
      {include && <details className={styles.details}>
        <summary className={styles.summary}>
          <Icons.chevron size={14} className={styles.chev} />
          <span className={styles.summaryText}>
            動作：{trimmed || "（還沒寫）"}
          </span>
        </summary>
        <div className={styles.editor}>
          <div className={panel.field}>
            <label htmlFor={`${id}-action`}>動作描述</label>
            <textarea
              id={`${id}-action`}
              rows={3}
              maxLength={MAX_ACTION}
              className={panel.textarea}
              value={action.value}
              aria-describedby={`${id}-action-hint ${id}-action-count`}
              onChange={(e) => action.setValue(e.target.value)}
              onBlur={() => quiet(action.save())}
            />
            <span id={`${id}-action-hint`} className={styles.hint}>
              {ACTION_HINT}
            </span>
            <span id={`${id}-action-count`} className={panel.counter}>
              {action.value.length} / {MAX_ACTION}
            </span>
          </div>
          <div className={chips.presets} role="group" aria-label="常用動作">
            {ACTIONS.map((a) => {
              const added = hasPhrase(action.value, a.phrase);
              const tooLong = !added && appendPhrase(action.value, a.phrase).length > MAX_ACTION;
              return (
                <button
                  key={a.phrase}
                  type="button"
                  className={chips.preset}
                  data-added={added}
                  disabled={added || tooLong}
                  aria-label={added ? `${a.label}（已加入）` : undefined}
                  title={tooLong ? `再加入會超過 ${MAX_ACTION} 字` : undefined}
                  onClick={() => void action.setNow(appendPhrase(action.value, a.phrase)).catch(() => {})}
                >
                  {added ? <span aria-hidden="true">✓ </span> : null}
                  {a.label}
                </button>
              );
            })}
          </div>
          <ExclusiveChips label="表情" group={EXPRESSIONS} text={action.value} onChange={(v) => void action.setNow(v).catch(() => {})} />
          <ExclusiveChips
            label="面向"
            group={FACINGS}
            text={action.value}
            onChange={(v) => void action.setNow(v).catch(() => {})}
            hint={FACING_HINT}
          />
          <p className={styles.hint}>改動作文字後按「換一張」。</p>
        </div>
      </details>}
    </section>
  );
}
