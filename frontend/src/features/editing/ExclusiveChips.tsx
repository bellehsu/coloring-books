import { useId } from "react";
import { activePhrase, setExclusivePhrase } from "@/lib/actions";
import { MAX_ACTION } from "@/lib/limits";
import chips from "./chips.module.css";

/** 單選的片語晶片群組（表情、面向）：再按一次作用中的取消；改選另一個會取代原本的。 */
export function ExclusiveChips({
  label, group, text, onChange, hint,
}: {
  label: string;
  group: { label: string; phrase: string }[];
  text: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  const hintId = useId();
  const active = activePhrase(text, group);
  return (
    <>
      <div className={chips.presets} role="group" aria-label={label} aria-describedby={hint ? hintId : undefined}>
        {group.map((g) => {
          const on = g.phrase === active;
          const tooLong = !on && setExclusivePhrase(text, group, g.phrase).length > MAX_ACTION;
          return (
            <button
              key={g.phrase}
              type="button"
              className={chips.preset}
              data-added={on}
              aria-pressed={on}
              disabled={tooLong}
              title={tooLong ? `再加入會超過 ${MAX_ACTION} 字` : undefined}
              onClick={() => onChange(setExclusivePhrase(text, group, on ? null : g.phrase))}
            >
              {on ? <span aria-hidden="true">✓ </span> : null}
              {g.label}
            </button>
          );
        })}
      </div>
      {hint ? (
        <p id={hintId} className={chips.hint}>
          {hint}
        </p>
      ) : null}
    </>
  );
}
