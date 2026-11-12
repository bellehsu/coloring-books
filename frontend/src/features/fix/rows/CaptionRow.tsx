import { useId, useState } from "react";
import type { Page } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { CaptionField, captionError } from "@/components/ui/CaptionField";
import { useToast } from "@/components/ui/Toast";
import chip from "@/features/editing/chips.module.css";
import { errMsg } from "@/features/editing/errMsg";
import { usePageText } from "@/features/editing/usePageText";
import { normalizeCaption } from "@/lib/limits";
import { useCaptionDisplay } from "../fixApi";
import { captionChoiceLabel, captionSummary } from "../fixState";
import styles from "../FixPage.module.css";
import { quiet, Row, useSavePage } from "./Row";

/**
 * 圖下方的字：「改字」打開中文／英文欄位；兩個開關（中文、英文，多選）決定這一頁印哪幾行，兩個都不選＝
 * 圖下方留一條空白讓小朋友自己寫；「把這個選擇套用到整本書」把這頁的開關套到每一頁。
 * busy（復原中、位置還在存、等著重新合成）時開關與套用停用；存的時候也停用，免得連按蓋來蓋去。
 */
export function CaptionRow({
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
