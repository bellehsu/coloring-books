# README demo book 「小狐狸的一天」 (master af163ef, real GPU)

Date 2026-11-12 ~21:30 Taipei. Separate acceptance environment (backend :8001 + Next dev :3001, own DB/storage under `storage/`, `storage.backend: local`), real ComfyUI 0.36.0 :8188, RTX 5080.

- Character: 「一隻圓圓胖胖的小狐狸，橘色的毛、白色的肚子，脖子上圍著藍色的小圍巾」 — 1 generation, 44 s.
- Story: 20 pages (scene / action / 中文 / 英文 on separate lines), see `readme_demo.mjs`.
- 開始做這本書 → all 20 pages 可以印 (PASSED, no 請看一下) in ~16 min (42 generations incl. character).
- PDF: 20 pages, 489 KB.
- Known content artifact: page 10 (午餐) background has AI-garbled text on a blackboard.
- Screenshots for README (dev indicator hidden / cropped) were copied to `docs/screenshots/` (commit c8b04b7). `readme_retake.mjs` re-shoots UI screens of the existing book with the Next dev indicator hidden.
