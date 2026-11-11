# Phase 10 Storage (R2 + invite identity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every binary file behind a `Storage` interface with Local and R2 implementations, make the engine exchange bytes, serve images through owner-checked 302s to short-lived signed URLs, and add invite-link identity with per-owner isolation, a daily generation cap, cleanup scripts and schema cleanup.

**Architecture:**
- `backend/app/storage.py` becomes a protocol plus two implementations:
  - `LocalStorage`, a folder whose signed URLs go through an HMAC route;
  - `R2Storage`, boto3 S3 with presigned GET, behind an on-disk LRU cache.
- All callers use `put/get/delete`, and nobody touches `Path` any more.
- Identity is an `owner` table plus `owner_session`, resolved per request by a `current_owner` FastAPI dependency.
- Books carry `owner_id`. Pages and generations inherit ownership through `book_id`.

**Tech Stack:** FastAPI, SQLModel/SQLite + Alembic, boto3 (new), moto (new, tests only), Pillow, Next.js 16, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-11-storage-r2-invites-design.md` (§4.8 is binding detail).

## Global Constraints

- **Worktree.** Work only in `/ai-data/repos/coloring-books-phase10` on branch `phase10-storage`.
  - Never modify `/ai-data/repos/coloring-books`. The user's dev servers (3000/8000) run from it, together with the real `storage/app.db` and `storage/books/`.
  - The worktree's `config/settings.yaml` is a copy. Do not commit it.
- **Tests and the e2e fake backend never need network or R2 credentials.** They use `LocalStorage`. `R2Storage` is tested with moto.
- **Credentials only through environment variables** (`APP_STORAGE__R2__ACCESS_KEY`, `APP_STORAGE__R2__SECRET_KEY`). Never in settings files, logs, error messages or commits.
- **Storage settings.**
  - Keys: `storage.backend: local | r2`, `storage.r2.endpoint`, `storage.r2.bucket`, `storage.signed_url_ttl_s` (default **120**), `storage.cache_dir`, `storage.cache_max_bytes` (default **2 GiB**).
  - The local folder root remains `paths.storage_dir`.
- **Object keys** keep today's relative-path format: `books/{book}/generations/{gen}.png`, `…_mask.png`, `books/{book}/pages/{page}/lineart_{key}.png`, `cache/previews/{gen}_{key}.png`.
- **Ownership.** Another owner's book, page or generation returns **404**. The admin sees everything. `/api/system/preflight` is admin-only (403 for others). `/api/system/queue` returns only counts and ETA.
- **Sessions.**
  - The cookie is named `cb_session`, httpOnly, SameSite=Lax, Path=/, and lasts 90 days.
  - The DB stores only `sha256(session_id)`.
  - Every request looks up the session and its owner. A revoked owner gets **401** with detail 「連結已失效」.
- **Daily cap.**
  - The default is **60**.
  - A generation is counted when it is enqueued, and refunded if it ends FAILED or CANCELLED.
  - The day boundary is **Asia/Taipei**.
  - Over the cap returns **429** with detail, verbatim: 「今天的生成次數已用完，明天再來」.
  - Compose and auto-select are not counted.
- **Image routes** check the owner, then return `302` to `storage.signed_url(key, ttl)` with header `Cache-Control: private, max-age=60`.
- **Retention.** Discarded generations older than **30 days** have their objects deleted by `scripts/storage_gc.py`.
- **Alembic 0006** drops exactly `page.sketch_path` and `page.controlnet_strength`, using batch mode with a reversible downgrade. `PageUpdate.controlnet_strength` is still accepted and ignored. `PageOut.sketch_url` is removed.
- **Commits.** Local only. Stage explicit paths. Trailer:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_01XPLcrggmWu3CZ2zyR3c9fz`
- **Test commands** (run in the worktree):
  - backend: `env -u PYTHONPATH PYTHONPATH=backend:. /ai-data/venvs/ubuntu24.04/coloring-books/bin/python -m pytest -q`. The venv's editable install points at the main checkout, so `PYTHONPATH=backend:.` is required.
  - frontend: `cd frontend && npx vitest run && npm run lint && npx tsc --noEmit`. Run `npm ci` first if `node_modules` is missing.
  - API types: `PYTHON=… npm run gen:api`.
  - e2e: `npx playwright test` on ports 3010/8010. Never 3000/8000.
- **Installing packages.** `pip install boto3 moto` into the venv is allowed. Add `boto3` to `pyproject.toml` dependencies and `moto` to the test extra.

## Review Focus

1. **Guessing another owner's ids on any endpoint** must return 404 and must not mutate anything. This covers book, page, generation, image/mask/cutout/lineart/print/pdf, compose, upload, batch, character and story text. → Task 5, one parametrised test over every route that takes an id.
2. **A revoked invite while a tab is open** must make the next API call return 401 「連結已失效」, and image 302s must stop. → Task 5.
3. **A tampered or expired `/api/files` signature** returns 403, and a key that escapes the root returns 403 or 404, never a file. → Task 3.
4. **An object missing from storage**, for example deleted by GC or by hand: image routes return 404 rather than 500, and line art regenerates instead of crashing. → Task 2 and Task 3.
5. **Cap edge cases:** 23:59 vs 00:00 Asia/Taipei, a failed job refunds, and two enqueues at the cap boundary must not both pass. → Task 6.

---

### Task 1: Storage interface, LocalStorage, R2Storage, cache, settings

**Files:**
- Modify: `backend/app/storage.py`. Keep the image helpers: `ImageRejected`, `fit_upload` and so on. Replace class `Storage`.
- Modify: `backend/app/config.py` (`StorageSettings`), `config/settings.example.yaml`, `pyproject.toml`.
- Test: `tests/test_storage_contract.py` (new), `tests/test_config.py`.

**Interfaces (produces):**
- `class StorageNotFound(KeyError)`
- `class Storage(Protocol)` with these methods:
  - `put(key: str, data: bytes, content_type: str = "image/png") -> None`
  - `get(key: str) -> bytes`
  - `delete(key: str) -> None`
  - `exists(key: str) -> bool`
  - `signed_url(key: str, ttl_s: int) -> str`
  - `list(prefix: str) -> Iterator[str]`
  - key builders `generation_image(book_id, gen_id)`, `generation_mask(...)`, `lineart(...)`, `preview(...)` (same strings as today)
  - `delete_prefix(prefix: str) -> None`
- `LocalStorage(root: Path, signing_key: bytes)`.
  - `signed_url` returns `/api/files/{key}?exp={unix}&sig={hex}`, where `sig = hmac_sha256(signing_key, f"{key}\n{exp}")`.
  - `verify(key, exp, sig, now) -> bool`
- `R2Storage(endpoint: str, bucket: str, access_key: str, secret_key: str, client=None)`: boto3 `s3` client with `signature_version="s3v4"`, `region_name="auto"`.
- `CachedStorage(inner: Storage, cache_dir: Path, max_bytes: int)`.
  - `get` reads through the cache. Writes go to a temp file and are then renamed atomically.
  - Eviction is oldest-first by mtime.
  - `put` and `delete` also update and invalidate the cache.
- `make_storage(settings) -> Storage`
  - `backend: local` gives `LocalStorage`.
  - `backend: r2` gives `CachedStorage(R2Storage)`.
  - The signing key comes from `APP_STORAGE__SIGNING_KEY`, or is generated once and stored at `<storage_dir>/.signing_key`.

- [ ] **Step 1: Write the failing tests.**
  - `test_storage_contract.py`, parametrised over `LocalStorage(tmp)`, `R2Storage` under `moto.mock_aws`, and `CachedStorage(LocalStorage)`:
    - put/get round-trip;
    - `get` of a missing key raises `StorageNotFound`;
    - `delete` of a missing key gives no error;
    - `exists`;
    - `list(prefix)` returns only matching keys;
    - `delete_prefix`;
    - keys containing `..` or starting with `/` raise `ValueError` on every method.
  - `test_local_signed_url_verifies`:
    - a good signature returns True;
    - a tampered key or sig returns False;
    - `exp` in the past returns False.
  - `test_r2_presigned_url_contains_bucket_and_expires`: check the moto URL query has `X-Amz-Expires=120`.
  - `test_cache_evicts_oldest_over_max` and `test_cache_atomic_write` (no partial file is left on a failing write).
  - `test_settings_storage_defaults`:
    - backend `local`, ttl 120, cache 2 GiB;
    - the env override `APP_STORAGE__BACKEND=r2`;
    - credentials are only read from env;
    - `repr(settings)` does not contain the secret.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement and add the dependencies (`pip install boto3 moto`).
  - Keep the old `Storage` class name importable as an alias of `LocalStorage` **only until Task 2 finishes**; Task 2 removes the alias.
  - Wire `app.state.storage = make_storage(settings)` in `main.py`.
- [ ] **Step 4:** The full backend suite passes, with existing callers still using the alias.
- [ ] **Step 5:** Commit `feat(storage): Storage protocol with Local, R2 and cached implementations`.

### Task 2: Callers use bytes; the engine takes bytes

**Files:**
- Modify:
  - `backend/app/services/engine.py` (`GenerationRequest.character_image: bytes | None`)
  - `backend/app/services/comfy_engine.py`
  - `backend/app/services/worker.py` (`decode_request` reads bytes; results are `put`)
  - `backend/app/services/page_lineart.py` (existence is decided from DB keys, with a fallback on `StorageNotFound`)
  - `backend/app/api/compose.py`, `lineart.py`, `export.py`, `pages.py` (cascade delete through `delete_prefix`), `generations.py` (cutout reads bytes)
  - `scripts/dev_fake_backend.py`
- Test: the existing suites, plus `tests/test_storage_callers.py` (new).

**Interfaces:**
- Consumes: the Task 1 `Storage`.
- Produces: no `storage.abs(` anywhere in `backend/app` (a grep-based test asserts this); the `Storage` alias is removed.

- [ ] **Step 1: Write the failing tests.**
  - `test_no_abs_calls`: grep `backend/app` for `.abs(`, which must give 0 hits.
  - `test_engine_receives_character_bytes`: a pose job with the fake engine sees `req.character_image == <bytes of the stored ref>`. `request_json` keeps the **key**.
  - `test_missing_generation_object_lineart_regenerates_or_404`: delete the object behind a composite. `GET /pages/{id}/lineart` must not 500; it returns 404 with the zh-Hant message 「圖片檔案遺失」.
  - `test_lineart_existence_uses_db_key`: wrap the storage in a spy. A second `ensure_page_lineart` with an unchanged key makes no `exists` call.
  - `test_delete_page_removes_objects`: after deleting the page, `list("books/{b}/pages/{p}/")` is empty and the page's generation objects are gone.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement. Pillow reads from `io.BytesIO(storage.get(key))`.
- [ ] **Step 4:** The full backend suite passes, and `npx tsc --noEmit` is unaffected.
- [ ] **Step 5:** Commit `refactor: storage callers and engine exchange bytes`.

### Task 3: Image routes redirect; `/api/files`

**Files:**
- Modify: `backend/app/api/generations.py` (image, mask), `backend/app/api/lineart.py` (lineart, preview), `backend/app/api/files.py` (new, `GET /api/files/{key:path}`), `backend/app/main.py`.
- Test: `tests/test_image_routes.py` (new).

**Interfaces:**
- Produces: image routes return `302` with `Location = storage.signed_url(key, settings.storage.signed_url_ttl_s)` and `Cache-Control: private, max-age=60`.
  - Under `LocalStorage` the Location is `/api/files/...`, which serves the bytes after `verify`.
  - Processed images (cutout, `print.png`, PDF, QA overlay) keep streaming bytes.

- [ ] **Step 1: Write the failing tests.**
  - `test_image_route_redirects_to_signed_url`: status 302, the headers, and following the redirect gives the same bytes.
  - `test_files_route_rejects_bad_sig_and_expired`: both return 403.
  - `test_files_route_rejects_escape`: a key with `..` returns 403/404.
  - `test_image_route_missing_object_404`.
  - An e2e-compatible check: the frontend `<img src="/api/generations/1/image">` still loads. Covered by an existing e2e in Task 8.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** The backend suite passes.
- [ ] **Step 5:** Commit `feat(api): image routes redirect to signed storage URLs`.

### Task 4: Schema 0006 (owner, session, usage, owner_id, discarded_at; drop sketch columns)

**Files:**
- Create: `backend/app/migrations/versions/0006_*.py` (follow the existing naming).
- Modify: `backend/app/models.py`, `backend/app/api/schemas.py` (remove `PageOut.sketch_url`; `PageUpdate.controlnet_strength` stays accepted and ignored).
- Regenerate: the frontend API types, and remove `sketch_url` from the frontend fixtures.
- Test: `tests/test_migrations.py`, `tests/test_models_storage.py`.

**Interfaces (produces):**
- `Owner(id, name: str, token_hash: str unique, is_admin: bool=False, daily_generation_cap: int=60, created_at, revoked_at: datetime|None)`
- `OwnerSession(id, owner_id FK, session_hash: str unique, created_at, last_seen)`
- `Usage(owner_id FK, day: date, generations: int)`, primary key `(owner_id, day)`
- `Book.owner_id: int` (FK, NOT NULL)
- `Generation.discarded_at: datetime | None`
- Migration upgrade, in order:
  1. create the tables;
  2. if any `book` rows exist, insert one admin owner (`name="admin"`, random token hash, `is_admin=1`) and set `book.owner_id` to it;
  3. make `owner_id` NOT NULL;
  4. backfill `discarded_at = updated_at or created_at` for already-discarded rows;
  5. drop the two page columns.
  The downgrade reverses all of this.

- [ ] **Step 1: Write the failing tests.**
  - `test_0006_upgrade_on_copy_of_phase9_db`: build a DB at 0005 with rows (books, discarded generations, sketch_path set), upgrade, and check:
    - owner_id is set to the admin;
    - discarded_at is backfilled;
    - the columns are gone;
    - FKs and indexes are intact.
  - `test_0006_downgrade_roundtrip`.
  - `test_0006_empty_db`: no books, so no admin is created.
  - `PageOut` has no `sketch_url`.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement, then run `gen:api`.
- [ ] **Step 4:** The backend suite passes, and the frontend tsc and vitest pass after the fixture update.
- [ ] **Step 5:** Commit `feat(db): owners, sessions, usage, discarded_at; drop sketch columns`.

### Task 5: Invite links, sessions, ownership on every route

**Files:**
- Create: `backend/app/services/auth.py`, `backend/app/api/invites.py` (`GET /i/{token}`).
- Modify: `backend/app/api/deps.py` (`current_owner`), every router that takes ids, `backend/app/main.py`, `backend/app/config.py` (`auth.dev_owner: str | None`, `auth.cookie_secure: bool=False`), `frontend/next.config.ts` (add a rewrite `/i/:path*` → backend), and a frontend page `frontend/src/app/invalid-link/page.tsx` showing 「連結已失效，請向邀請你的人要新的連結」.
- Test: `tests/test_auth.py` (new), `tests/test_ownership.py` (new), and existing API tests, which get an owner fixture.

**Interfaces:**
- Consumes: Task 4 models.
- Produces:
  - `hash_token(s: str) -> str` (sha256 hex)
  - `create_owner(session, name, *, is_admin=False, cap=60) -> tuple[Owner, str token]`
  - `start_session(session, owner) -> str session_id`
  - `current_owner(request, session) -> Owner`
  - `owned_book(session, owner, book_id) -> Book` (404 if not owned unless admin)
  - `owned_page(...)`, `owned_generation(...)`
  - `GET /i/{token}`:
    - valid: set the cookie and redirect 303 to `/`;
    - invalid or revoked: 303 to `/invalid-link`.
  - `current_owner`:
    - with no cookie and `auth.dev_owner` set, returns or creates that admin owner (name = dev_owner);
    - otherwise no or invalid cookie gives 401 「請用邀請連結開啟」, and a revoked owner gives 401 「連結已失效」.
  - Book creation sets `owner_id`. Book lists are filtered by owner (admin sees all).

- [ ] **Step 1: Write the failing tests.**
  - `test_invite_link_sets_cookie_and_redirects`: the cookie flags are httpOnly, SameSite=Lax, max-age 90 days, and the DB stores only the hash.
  - `test_revoked_owner_gets_401_next_request` (Review Focus 2), including image routes.
  - `test_ownership_404_on_every_route` (Review Focus 1): parametrise over every route that takes a book, page or generation id, read from `app.routes`, so a newly added route is covered automatically.
    - Owner A creates data and owner B requests it: 404, and the DB is unchanged.
    - The admin gets 200.
  - `test_dev_owner_fallback`
  - `test_preflight_admin_only`: 403 for non-admins.
  - `test_queue_has_no_content`
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement.
  - In existing tests, use a pytest fixture that sets `auth.dev_owner` by default, so old tests keep passing.
  - The ownership tests use two real invites.
- [ ] **Step 4:** The backend suite passes. Then run `gen:api`; the frontend tsc and vitest pass.
- [ ] **Step 5:** Commit `feat(auth): invite links, sessions and per-owner isolation`.

### Task 6: Daily generation cap

**Files:**
- Create: `backend/app/services/usage.py`.
- Modify: the enqueue paths (character candidates, background, pose, materials batch) and the worker's finish (refund on FAILED/CANCELLED); `generations.py` cancel.
- Test: `tests/test_usage.py` (new).

**Interfaces (produces):**
- `taipei_day(now: datetime) -> date`
- `reserve(session, owner, n: int, now) -> None`. It raises `HTTPException(429, …)` with the detail copied verbatim from the Global Constraints: 「今天的生成次數已用完，明天再來」.
  - It is atomic: a conditional UPDATE `generations + n <= cap`, or INSERT if no row exists.
- `refund(session, owner_id, day, n=1)`. The generation records its usage day in `request_json` under `"usage_day"` (an extra provenance key) so the refund hits the right day.

- [ ] **Step 1: Write the failing tests.**
  - `test_cap_429_at_61st`, which checks the exact message.
  - `test_failed_and_cancelled_refund`.
  - `test_taipei_midnight_boundary`: 15:59:59Z is the old day, 16:00:00Z is the new one.
  - `test_batch_reserves_n_atomically`: a batch that would exceed the cap queues nothing and returns 429.
  - `test_concurrent_reserve_never_exceeds_cap`: two threads at cap−1, so exactly one passes.
  - `test_compose_not_counted`.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** The full backend suite passes.
- [ ] **Step 5:** Commit `feat(usage): per-invite daily generation cap`.

### Task 7: Invites CLI, cleanup script, discarded_at

**Files:**
- Create: `scripts/invites.py`, `scripts/storage_gc.py`.
- Modify: the discard path (set and clear `discarded_at` on PATCH `discarded`), `README.md` (usage).
- Test: `tests/test_scripts.py`.

**Interfaces:**
- `invites.py create --name N [--admin] [--cap 60] [--base-url URL]` prints the link once.
- `invites.py list` shows name, cap, today's usage and the revoked flag, and never shows a token.
- `invites.py revoke --id`.
- `invites.py delete-data --id [--yes]` deletes books, rows and objects (`delete_prefix("books/{id}/")`) for each of that owner's books, then the owner. It asks for confirmation unless `--yes`.
- `storage_gc.py [--dry-run] [--days 30]`:
  - deletes the objects of generations with `discarded_at < now-30d` and clears their keys;
  - deletes orphan objects under `books/` that no row references;
  - prints counts.

- [ ] **Step 1: Write the failing tests.**
  - create then list (the token is not printed in list);
  - revoke, then the session is 401;
  - delete-data removes rows and objects and leaves other owners untouched;
  - gc deletes only expired discarded objects plus orphans, and `--dry-run` deletes nothing;
  - discarding sets `discarded_at`, and restoring clears it.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** The backend suite passes.
- [ ] **Step 5:** Commit `feat(scripts): invites CLI and storage GC`.

### Task 8: Frontend, e2e, docs, config

**Files:**
- Modify:
  - the frontend 401/429 handling: a global `ApiError` 401 sends the user to `/invalid-link`, and 429 shows its detail in the existing toast;
  - remove the `sketch_url` leftovers;
  - `frontend/e2e/*`: seed through an invite. The fake backend gets `--dev-owner admin`, plus a test-only route or CLI to create a second invite.
  - `scripts/dev_fake_backend.py`;
  - `README.md`: storage settings, R2 setup steps for the user, invites, GC, the new local DB path;
  - `config/settings.example.yaml`;
  - new `deploy/litestream.yml` (prod config, not enabled);
  - `CLAUDE.md`: the 路徑設定 section gains the `storage.*` keys. This is a user file; change only that list.

- [ ] **Step 1: Write the failing tests.**
  - vitest: a 401 redirects to `/invalid-link`; a 429 toast shows 「今天的生成次數已用完，明天再來」.
  - e2e `isolation.spec.ts`: invite A creates a book, invite B (in a fresh browser context via `/i/{token}`) sees no books, and B gets 404 when opening A's editor URL.
  - The full existing e2e passes, with images loading through 302 → `/api/files`.
- [ ] **Step 2:** Run them and confirm they fail.
- [ ] **Step 3:** Implement and write the docs.
  - README "R2 準備" lists exactly what the user must create: the Cloudflare account, buckets `coloring-books-dev` / `coloring-books-prod`, a per-bucket token, the CORS rule allowing GET from the site origin, and env vars in `scripts/dev.env`.
  - It also covers the new local DB path, e.g. `paths.database: storage-r2/app.db`.
- [ ] **Step 4:** Backend, vitest, lint, tsc and e2e all pass.
- [ ] **Step 5:** Commit `feat: invite-aware frontend, e2e isolation, storage docs`.

### Task 10: Sharing finished books (spec §4.9; added 2026-11-11 by user decision)

**Files:**
- Create:
  - `backend/migrations/versions/0008_book_shared_at.py`
  - `backend/app/api/shared.py`
  - `backend/tests/test_sharing.py`
  - `frontend/src/app/shared/[id]/page.tsx` (viewer)
  - `frontend/e2e/sharing.spec.ts`
- Modify:
  - `backend/app/models.py` (`Book.shared_at`)
  - `backend/app/api/books.py` (`PUT /books/{id}/share`)
  - `backend/app/api/schemas.py` (`BookOut.shared_at`, `ShareIn`, `SharedBookOut`, `SharedBookDetailOut`)
  - `backend/app/main.py` (router)
  - `backend/app/api/export.py`: extract the "PDF of completed pages" and "composed page image" helpers so `shared.py` reuses them, and don't duplicate the code
  - frontend home page (two sections), the export drawer (switch), generated API types, `tests/test_migrations.py`

**Interfaces:**
- Consumes:
  - `current_owner`, `owned_book` (deps.py)
  - `PageStatus.PASSED`
  - the existing PDF and composed-page code in export.py
  - `Owner.name`, `Owner.revoked_at`
- Produces: the routes and shapes exactly as in spec §4.9.
  - 404 detail: 「找不到這本分享的繪本」
  - 409 detail: 「至少完成一頁才能分享」

- [ ] **Step 1: Write failing backend tests (`test_sharing.py`).**
  - Toggling the share:
    - Owner A with 0 passed pages: `PUT share {shared:true}` → 409. After one page passes → 200 and `shared_at` is set.
    - B toggling A's book → 404. Admin → 200.
  - `GET /api/shared/books`:
    - As B, it lists A's shared book with `author == A.name`, `page_count == passed pages`, `mine == false`.
    - It excludes unshared books, books with 0 passed pages, books of revoked owners, and B's own shared book.
  - Detail, image and PDF:
    - The detail lists only passed pages, in position order.
    - The image of a non-passed page → 404.
    - `image.png` → 200 image/png, long edge ≤ 1600, `Cache-Control: private, max-age=300`.
    - The PDF → 200 `%PDF`, containing exactly the passed pages (assert the page count).
  - Turning sharing off, then B's detail, image and PDF → 404 on the next request. Revoking A → 404.
  - Without a session (dev_owner off) → 401.
  - B viewing and downloading doesn't change `usage` for anyone.
  - Migration 0008 upgrades and downgrades.
- [ ] **Step 2: Write failing frontend tests.**
  - vitest:
    - home renders 「大家的繪本」 items and the empty state;
    - the 「已分享」 badge;
    - the export drawer switch is disabled with 「至少完成一頁才能分享」 when 0 pages are passed, and calls `PUT share` when toggled;
    - the viewer renders 「作者：…」, the pages and 「下載 PDF」;
    - a viewer 404 shows 「這本繪本已經不再分享」.
  - e2e `sharing.spec.ts`:
    - A passes 1 of 2 pages and shares.
    - B (a second invite through the fake backend's `/api/_test/invite`) sees the book under 大家的繪本, opens `/shared/{id}`, sees exactly 1 page image, and the PDF download is `%PDF`.
    - A unshares, and B's reload shows 「這本繪本已經不再分享」.
- [ ] **Step 3:** Run the tests and confirm they fail for the right reason.
- [ ] **Step 4: Implement.**
  - Keep the shared routes in their own router. Never loosen `owned_*`.
  - Reuse the export helpers, don't copy them.
  - Regenerate the frontend API types the way the repo already does.
- [ ] **Step 5:** Backend pytest, vitest, lint, tsc and e2e all pass, and the viewer has 0 px horizontal overflow at 390 px.
- [ ] **Step 6:** Commit `feat: share finished books with other invited users`.

### Task 9: Real-R2 acceptance (the controller runs this, and only after the user provides dev-bucket credentials)

- [ ] Ask the user for confirmation that `coloring-books-dev` exists and that the credentials are in the worktree's `scripts/dev.env`. **Never read or print the secret**; check presence only.
- [ ] Use a fresh local DB path in the worktree, backend 8001 with `storage.backend: r2` and frontend 3001.
  - Create an admin invite and open the link.
  - Run one book through the full flow. The GPU is optional: ask the user, or use the fake backend with R2.
  - Check that the objects exist in the bucket, that `<img>` loads via a 302 to an R2 presigned URL, and that the presigned URL fails after the TTL.
  - Check that the PDF exports, then run `storage_gc.py --dry-run`.
- [ ] Record the results in `experiments/results/phase10_storage_check_<ts>/NOTES.md` and commit.

---

## Self-Review record
- **Spec coverage:**
  - §4.1 → Task 1
  - §4.2 → Task 2
  - §4.3 and §4.8 redirect → Task 3
  - §4.4 → Tasks 5–7
  - §4.5 → Task 7
  - §4.6 → Task 4, plus Task 8 (Litestream config)
  - §4.7 → Task 8 docs
  - §5 → Tasks 1–9
- **Names are consistent:** `Storage`, `LocalStorage`, `R2Storage`, `CachedStorage`, `make_storage`, `StorageNotFound`, `current_owner`, `owned_book`, `reserve`, `refund`, `taipei_day`, `cb_session`.
