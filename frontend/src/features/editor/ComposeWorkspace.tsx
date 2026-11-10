"use client";

import { useId, useRef, useState, type ChangeEvent } from "react";
import Link from "next/link";
import type { Generation, Page } from "@/api/client";
import { useBook, useGenerateBackground, useGeneratePose, useUpdatePage, useUploadBackground } from "@/api/hooks";
import { Button } from "@/components/ui/Button";
import { Icons } from "@/components/ui/Icons";
import { useToast } from "@/components/ui/Toast";
import { ACTIONS, appendPhrase, hasPhrase } from "@/lib/actions";
import { errMsg } from "./errMsg";
import { thumbNumber, Thumbs, useThumbList } from "./Thumbs";
import panel from "./Inspector.module.css";
import styles from "./ComposeWorkspace.module.css";

export const MAX_SCENE = 800;
export const MAX_ACTION = 300;
export const UPLOAD_ACCEPT = "image/png,image/jpeg,image/webp";
export const UPLOAD_HINT = "線條簡單的插畫效果最好；照片擷取的線稿可能很亂，需要多補線。";

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
}

/**
 * 「① 構圖」的右欄：背景卡片與角色動作卡片。
 * 背景：縮圖（單選）、「換一張」（Qwen，用本頁場景文字）、「上傳背景圖」、收合的場景文字編輯。
 * 角色動作：縮圖、「換一張」（精準，用本頁動作文字；頁面沒寫就用最新一張的動作）、收合的動作文字編輯與常用動作。
 * 改過的文字在離開欄位或按「換一張」時存回頁面（PATCH），存好才產生。
 */
export function ComposeWorkspace(props: ComposeWorkspaceProps) {
  return (
    <>
      <BackgroundCard {...props} />
      <PoseCard {...props} />
    </>
  );
}

/**
 * 頁面上的一個文字欄位：沒改過就顯示伺服器的值；改過的值在 save() 時送出（同一個值不重送，
 * 送出中的請求會被等待）。失敗時 toast，save() reject，下次會重送。
 */
function usePageText(serverValue: string, send: (v: string) => Promise<unknown>) {
  const [edited, setEdited] = useState<string | null>(null);
  const sent = useRef<{ value: string; p: Promise<unknown> } | null>(null);
  const value = edited ?? serverValue;

  const save = (): Promise<unknown> => {
    if (edited === null) return Promise.resolve();
    if (sent.current?.value === edited) return sent.current.p;
    if (!sent.current && edited === serverValue) return Promise.resolve();
    const p = send(edited);
    const entry = { value: edited, p };
    sent.current = entry;
    p.catch(() => {
      if (sent.current === entry) sent.current = null;
    });
    return p;
  };
  return { value, setValue: setEdited, save };
}

/** 一次只跑一個（整段 await 期間停用按鈕；ref 擋住同一輪 render 前的第二次點擊）。 */
function useSingleFlight() {
  const [busy, setBusy] = useState(false);
  const ref = useRef(false);
  const run = async (fn: () => Promise<unknown>) => {
    if (ref.current) return;
    ref.current = true;
    setBusy(true);
    try {
      await fn();
    } finally {
      ref.current = false;
      setBusy(false);
    }
  };
  return { busy, run };
}

const quiet = (p: Promise<unknown>) => p.catch(() => {});

function useSavePage(bookId: number, pageId: number) {
  const toast = useToast();
  const update = useUpdatePage(bookId);
  return (body: { scene_prompt?: string; action_prompt?: string }) =>
    update.mutateAsync({ pageId, ...body }).catch((e: unknown) => {
      toast.show(errMsg(e, "無法儲存"), "error");
      throw e;
    });
}

function BackgroundCard({
  bookId,
  page,
  background,
  onPickBackground,
  picking,
  beforeReplaceBackground,
  onBackgroundUploaded,
}: ComposeWorkspaceProps) {
  const id = useId();
  const toast = useToast();
  const savePage = useSavePage(bookId, page.id);
  const scene = usePageText(page.scene_prompt, (v) => savePage({ scene_prompt: v }));
  const generate = useGenerateBackground(bookId);
  const upload = useUploadBackground(bookId);
  const flight = useSingleFlight();
  const uploading = useSingleFlight();
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
        .mutateAsync({ pageId: page.id, style: "simple" })
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

  return (
    <section aria-labelledby={`${id}-h`} className={styles.card}>
      <div className={styles.head}>
        <h2 id={`${id}-h`} className={styles.title}>
          背景
        </h2>
        <span className={styles.info}>{info}</span>
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
      </div>
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

function PoseCard({ bookId, page, pose, onPickPose, picking }: ComposeWorkspaceProps) {
  const id = useId();
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
        .mutateAsync({ pageId: page.id, action: trimmed, mode: "precise" })
        .catch((e: unknown) => toast.show(errMsg(e, "無法產生角色動作"), "error"));
    });

  return (
    <section aria-labelledby={`${id}-h`} className={styles.card}>
      <div className={styles.head}>
        <h2 id={`${id}-h`} className={styles.title}>
          角色動作
        </h2>
        <span className={styles.info}>{info}</span>
      </div>
      <Thumbs bookId={bookId} kind="pose" pageId={page.id} selectedId={page.pose_generation_id} onPick={onPickPose} picking={picking} />
      <Button
        aria-label="換一張角色動作"
        disabled={!hasCharacter || !trimmed || flight.busy || generate.isPending}
        onClick={() => void regenerate()}
      >
        換一張
      </Button>
      {book && !hasCharacter && (
        <span className={styles.hint}>
          <Link href={`/books/${bookId}/character`}>先設定角色</Link>
        </span>
      )}
      <details className={styles.details}>
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
              aria-describedby={`${id}-action-count`}
              onChange={(e) => action.setValue(e.target.value)}
              onBlur={() => quiet(action.save())}
            />
            <span id={`${id}-action-count`} className={panel.counter}>
              {action.value.length} / {MAX_ACTION}
            </span>
          </div>
          <div className={panel.presets} role="group" aria-label="常用動作">
            {ACTIONS.map((a) => {
              const added = hasPhrase(action.value, a.phrase);
              const tooLong = !added && appendPhrase(action.value, a.phrase).length > MAX_ACTION;
              return (
                <button
                  key={a.phrase}
                  type="button"
                  className={panel.preset}
                  data-added={added}
                  disabled={added || tooLong}
                  aria-label={added ? `${a.label}（已加入）` : undefined}
                  title={tooLong ? `再加入會超過 ${MAX_ACTION} 字` : undefined}
                  onClick={() => action.setValue(appendPhrase(action.value, a.phrase))}
                >
                  {added ? <span aria-hidden="true">✓ </span> : null}
                  {a.label}
                </button>
              );
            })}
          </div>
          <p className={styles.hint}>改動作文字後按「換一張」。</p>
        </div>
      </details>
    </section>
  );
}
