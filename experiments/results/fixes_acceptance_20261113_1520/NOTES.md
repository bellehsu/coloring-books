# Fix round 2026-11-13 — real-UI acceptance (Task 16)

Branch fixes-2026-11-13 (aad0dfa + pose-row wrap). Separate servers: backend :8001 (local storage under this folder,
own app.db), frontend `next dev -H 0.0.0.0 -p 3001`, real ComfyUI :8188 (medium outline wording). Driven by
`fixes_walk.mjs` (Playwright), steps run one at a time. Book: 小狐狸驗收, 3 pages, A4 家用.

| step | result |
|---|---|
| setup: 2 character candidates, × on #2 → toast 已刪掉這張候選 → 復原 brings it back; × again; choose; paste 3 pages; 開始做 | PASS (first produce ≈ 3 min) |
| B progress line | PASS 「做好 3 / 3 頁」 |
| B 選取多頁: pages 1+2, 每頁 2 張 → 重畫 2 頁的主角 | PASS both pages 請看一下 + note, still counted ready (3/3), 請看一下 2 / 可以印 3 |
| C page 1: history row (3 poses, 使用中), click another → swaps; ↶ 復原 → back; 一次畫 1 張 重畫主角 → 4 poses | PASS (≈ 30 s) |
| setup 編輯 page 3 動作 → 要重畫 chip; B 「還有 1 頁要重畫」 → 繼續做 | PASS only page 3 got a new pose; tagged 請看一下 |
| line art of redrawn pages | single outlines (18_c_page3_lineart, 13b) |
| 複製給 AI 的指令 via http://u2624-DIY-PC:3001 (insecure origin) | PASS execCommand fallback → 「已複製…」 toast (dialog path covered by e2e) |
| 印出這本書 → 看好了，下一步 → 下載 PDF（3 頁） | PASS review pages included; 69 KB PDF |
| share on, then bookshelf ⋯ → 刪除這本書 → confirm 刪除 | PASS toast 已刪除「小狐狸驗收」; shared GET 404; book GET 404; storage books/1/ removed |

Found and fixed during the walk: C's pose history row scrolled horizontally after 4 poses (older ones hidden) → now wraps
like mockup B (FixPage.module.css `.poses`).
Note: first-visit pages show no 「新」 badge (by design: high-water mark starts on first visit).
Screenshots: shots/01–23 (+13b). GPU freed with POST /free afterwards; acceptance servers stopped.
