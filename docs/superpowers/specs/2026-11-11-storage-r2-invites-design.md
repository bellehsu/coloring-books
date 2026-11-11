# Phase 10: storage (R2 object storage + invite identity): design

Date: 2026-11-11. This follows Phase 9 (all-Qwen, merged 4656443). Roadmap: Phase 9 consolidation → **storage (this phase)** → cloud (diffusers + Modal + always-on web host).

## 1. Why

The cloud trial (5–10 friends or parents) has three needs:
- testers' images must live somewhere reliable that does not depend on a single machine's disk;
- each person sees only their own books;
- the GPU, which will run on Modal, must not touch storage.

Today every file lives in the local `storage/books/**` folder. Measured: each PNG is about 800 KB, a 10-page book is about 40–50 MB, and the trial would total about 1 GB. The code reads and writes the disk directly through `Storage.abs()`/`write()` (about 27 call sites), and the engine request passes in **file paths**.

## 2. User decisions (2026-11-11)

| Topic | Decision |
|---|---|
| Image location | **R2 object storage from day one** (Cloudflare R2) |
| Delivering images to the browser | **Short-lived signed URLs, fetched directly from R2** |
| Local development / your own PC | **Local also uses R2** |
| Separating environments | **Two buckets: dev and prod** |
| Existing local data (`storage/`, 2 books) | **Not migrated; local starts from empty.** The existing files and database are kept untouched as a backup |
| Automated tests and the e2e fake backend | **Local-folder implementation**, no network or R2 credentials needed |
| Retention | **Discarded images are deleted 30 days after being discarded.** Everything else is kept |
| After the trial ends | **Kept until the user deletes it by hand.** There is a command to delete everything under one invite |
| Identity / permissions | **The invite link is the identity.** No accounts or passwords. Each link sees only its own books; the user has an admin identity that sees all books |

## 3. Goals, non-goals, anti-goals

**Goals**
- Every binary file (generated images, masks, composites, line art, uploads, previews) goes into object storage. The database stores only text, settings and **object keys**.
- One `Storage` interface with two implementations: `LocalStorage` (folder; tests, the fake backend) and `R2Storage` (S3-compatible). Selected by `settings.yaml`.
- The browser gets images through short-lived signed URLs.
- Engine requests and responses carry **image bytes** rather than paths, so the generation layer never touches storage. This is a precondition for the cloud phase.
- Invite identity: `owner_id` on books. Every API call checks ownership. An admin identity can see everything.
- Automatic cleanup: discarded images are deleted after 30 days, and orphan objects are removed.
- Database schema cleanup: drop the columns left over from Phase 9.

**Non-goals**
- Choosing and deploying the host, Modal, or diffusers. These belong to the cloud phase. This phase only makes sure the design doesn't lock them in.
- Accounts, passwords, OAuth, payment.
- Migrating existing local data.
- Moving off SQLite. SQLite stays, single worker.

**Anti-goals**
- Credentials (R2 keys, invite tokens) never go into the repo, logs or error messages.
- Tests and the e2e fake backend never need network access or R2 credentials. CLAUDE.md requires them to be mockable.
- No image bytes are stored in the database.
- No tester can see or touch another person's book. That includes guessing IDs and reusing old signed URLs once they have expired.
- The user's existing `storage/app.db` and `storage/books/` are never modified or deleted.

## 4. Design

### 4.1 Storage interface
```
class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None
    def get(self, key: str) -> bytes              # missing → raises StorageNotFound
    def delete(self, key: str) -> None             # missing → no error
    def exists(self, key: str) -> bool
    def signed_url(self, key: str, ttl_s: int) -> str
    def list(self, prefix: str) -> Iterable[str]   # for orphan cleanup
```
- **Keys:**
  - `books/{book_id}/generations/{gen_id}.png`
  - `…/{gen_id}_mask.png`
  - `books/{book_id}/pages/{page_id}/lineart_{key}.png`
  - previews and so on, reusing today's relative paths.
  - The database stores keys, which are today's relative paths, so the format does not change.
- **`LocalStorage(root)`:**
  - files under `root`;
  - `signed_url` returns a backend route `/api/files/{key}?exp=…&sig=…`, HMAC-signed with a local key and verified on that route;
  - used by tests and the fake backend.
- **`R2Storage(endpoint, bucket, access_key, secret_key)`:**
  - uses boto3 (S3 API), which needs adding as a dependency;
  - `signed_url` is an S3 presigned GET.
- **Settings:**
  - `storage.backend: local | r2`
  - `storage.r2.endpoint`, `storage.r2.bucket`
  - credentials only through environment variables, `APP_STORAGE__R2__ACCESS_KEY` / `…__SECRET_KEY`
  - `storage.signed_url_ttl_s` (default 600)
- **Local cache:** line art, QA, compositing and export need the original images.
  - `get()` goes through a size-capped local disk cache (LRU, path set in settings, default 2 GB).
  - The cache can be deleted at any time.
  - `LocalStorage` doesn't need the cache.

### 4.2 Engine passes bytes
- `GenerationRequest.character_image: bytes | None` (formerly a path).
- `GenerationResult` keeps `image: bytes` and `mask: bytes | None`, as today.
- The worker reads the character reference image from Storage and sends it in. When the result comes back, the worker `put`s it into Storage and writes the key to the database.
- `request_json` still records the **key** of the character reference image, for provenance, not the bytes.

### 4.3 Image URLs
- `GenerationOut.image_url`, `mask_url`, `cutout_url`, `PageOut` line-art URLs and so on all become `storage.signed_url(key)`.
- The frontend already uses the URLs returned by the API, so it barely needs to change.
- **Expiry handling:**
  - the frontend refreshes queries before URLs expire, or retries once on a 403 with `onError` → refetch;
  - TTL 10 minutes; query `staleTime` is kept well under the TTL.
- **Images that go through processing** (cutout preview, `print.png`, PDF) are still produced by the backend and streamed back as a response, after an ownership check.

### 4.4 Invite identity
- **New tables:**
  - `owner` (`id`, `name`, `token_hash`, `is_admin`, `daily_generation_cap`, `created_at`, `revoked_at`);
  - `usage` (`owner_id`, `day`, `generations`).
- **Link flow:**
  - `/i/{token}`: the backend checks the token hash, sets an httpOnly, SameSite=Lax cookie (a server-side session or a signed owner_id), then redirects to `/`.
  - Revoked or unknown tokens → a 「連結已失效」 page.
- **Data:** `book.owner_id` (NOT NULL; existing rows are assigned to the admin).
- **Every API endpoint** goes through `current_owner`:
  - books, pages and generations are filtered by owner;
  - another person's resources return 404, not 403, so their existence is not revealed;
  - the admin sees everything.
- **Daily cap:** each generation enqueue (character / background / pose / batch materials) is counted. Over the cap → 429 with the zh-Hant message 「今天的生成次數已用完，明天再來」. Default 60 per day, adjustable per invite.
- **Management:** CLI scripts `scripts/invites.py create|list|revoke|delete-data`.
  - `create` prints the link once.
  - `delete-data` deletes all books, rows and objects under that invite, after confirmation.
- **Local development:** when `auth.dev_owner` is set in settings, requests without a cookie are treated as that owner (the admin), so local development needs no link.

### 4.5 Cleanup
- **`scripts/storage_gc.py`**, run on a schedule; the cloud phase decides the scheduling mechanism:
  - deletes the image objects of generations that have been `discarded` for more than 30 days, and clears their keys;
  - deletes orphan objects that no database row references, with `--dry-run`;
  - discarding has to record the time, so add `generation.discarded_at`.
- **Cascade deletion:** deleting a page or book deletes its objects too. Today it deletes files; it switches to `storage.delete`.

### 4.6 Database
- Stays on SQLite.
- **Backups:** the prod database is replicated continuously to an R2 prefix with Litestream. The config and docs are written in this phase; enabling it happens in the cloud phase.
- **Alembic 0006:**
  - add `owner`, `usage`, `book.owner_id` and `generation.discarded_at`;
  - **drop** `page.sketch_path`, `page.controlnet_strength` and other columns confirmed unused since Phase 9;
  - existing rows (if any) are assigned to the admin.
- **Local starts from empty:** settings point to a new database path (`storage-r2/app.db` or similar), so the existing `storage/app.db` is left untouched.

### 4.7 What the user needs to prepare (listed for them, never done for them)
- A Cloudflare account. Create R2 buckets `coloring-books-dev` and `coloring-books-prod`, and an API token for each with read/write on that bucket only.
- Put the credentials in the local `scripts/dev.env`, which is not version-controlled.
- Bucket CORS must allow GET from the frontend origin, for signed URLs. The docs give the configuration.

### 4.8 Details settled after the builder check
- **Same origin:** the frontend already proxies `/api/*` to the backend through Next.js rewrites (`next.config.ts`).
  - `/i/:token` is added to the rewrite the same way. FastAPI checks the token, sets the cookie and redirects to `/`.
  - Browser and API share one origin, so a SameSite=Lax httpOnly cookie works directly.
  - The cloud phase keeps the same arrangement: frontend and API on one domain.
- **Image URLs stay stable and redirect (replaces "the API returns signed URLs" in §4.3):**
  - The existing routes stay unchanged: `/api/generations/{id}/image`, `…/mask`, `/api/pages/{id}/lineart?v=…` and so on.
  - Each route **checks the cookie and the owner first**, then `302`s to `storage.signed_url(key, ttl)`.
  - Images still download **directly from R2**, so the user's decision is unchanged.
  - Benefits:
    - the frontend barely changes;
    - every view re-checks permissions, so an expired cookie or a revoked invite stops access straight away;
    - expiry needs no special handling.
  - The signed URL is used immediately, so the TTL is lowered to **120 seconds**.
  - The 302 carries `Cache-Control: private, max-age=60`, so the browser reuses the redirect for a short while.
  - Images the backend processes (cutout, `print.png`, PDF) are still streamed directly after the ownership check.
  - `LocalStorage`'s `/api/files/...` is HMAC-checked only. It is used in tests and with the fake backend.
- **Line-art existence check:** decide from the database (`page.lineart_key`/`lineart_path`) rather than calling `exists()` on every request. Fall back to regenerating only if a read fails with `StorageNotFound`.
- **Ownership scope:**
  - Only books have `owner_id`. Pages, generations and characters all belong to a book (characters live in the `book.character_*` fields), so ownership flows down through `book_id`.
  - `/api/system/queue` returns only counts and the ETA, with no content.
  - `/api/system/preflight` is **admin only**.
- **Columns dropped in 0006:** exactly `page.sketch_path` and `page.controlnet_strength`.
  - Use SQLite batch mode. The downgrade recreates them, nullable or with their old default.
  - `PageUpdate.controlnet_strength` stays accepted-and-ignored.
  - `PageOut.sketch_url` is removed, and the generated frontend types are updated.
  - The "existing rows go to the admin" rule only matters when migrating an old database, for example a copy in tests. Locally the database starts empty.
- **Sessions:**
  - The cookie holds a random session id.
  - The `owner_session` table stores only its hash, plus `owner_id`, `created_at` and `last_seen`.
  - **Each request looks up the session and the owner.** A revoked owner, or a session that has been deleted, takes effect immediately. Session lifetime is 90 days.
- **Daily cap:**
  - Generations are counted **when queued**.
  - A job that ends **failed or cancelled is refunded**.
  - The day boundary is **Asia/Taipei**.
  - Auto-select and composing (`compose`) are not counted, because they don't use the GPU. Pressing regenerate after a failure counts as a new job.
- **Cache:**
  - The local cache is only an accelerator; losing it does no harm, so it can sit on temporary disk.
  - Writes go to a temporary file and are then renamed atomically.
  - Eviction removes the oldest files first. An open file is unaffected (POSIX).

## 5. Success criteria
- **Unit tests (mocked):**
  - both implementations satisfy the same contract tests: put / get / delete / exists / list / signed_url;
  - `R2Storage` tested against moto, or a stub of the boto3 client;
  - the engine passes bytes;
  - ownership filtering, including 404 when guessing someone else's ID;
  - the daily cap gives 429;
  - the invite link sets the cookie, and revoked links are rejected;
  - cleanup deletes only expired discarded images and orphan objects;
  - cascade deletion;
  - 0006 migration upgrade and downgrade.
- **e2e (fake backend + LocalStorage):** the full flow still passes; two different invites cannot see each other's books.
- **Real run (the user provides dev bucket credentials):** start the app locally with the R2 dev bucket, run one book's full flow, and confirm:
  - images are in the bucket;
  - the browser loads them through signed URLs;
  - an expired URL can no longer be opened;
  - the PDF exports.

## 6. Calls made by the author (not decided by the user)
- Keys reuse today's relative path format.
- `LocalStorage`'s signed URLs go through a backend HMAC route.
- Local image cache defaults to 2 GB.
- Signed URL TTL defaults to 10 minutes.
- Another person's resources return 404.
- Daily cap defaults to 60 per day.
- `delete-data` asks for confirmation.
- Cleanup is a script; its schedule is decided in the cloud phase.
- Litestream config is written in this phase and enabled in the cloud phase.
- The local new database lives at a new path.
- boto3 is added as a dependency.
