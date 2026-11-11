#!/usr/bin/env python3
"""清理儲存空間（排程執行；排程方式由雲端階段決定）。

1. 過期的捨棄圖：`discarded_at` 早於 --days 天（預設 30）的生成紀錄，刪掉它的圖片、遮罩與預覽物件，
   並清空 image_path／mask_path。頁面選用中（selected／background／pose）、書的角色、
   排隊中任務要用的角色圖，捨棄再久也不刪。
2. 孤兒物件：books/ 底下沒有任何資料列指到的物件（生成圖、遮罩、頁面線稿 lineart_path），
   以及 cache/previews/ 底下生成紀錄已不存在（或已沒有圖）的預覽。
   正在寫入的物件不算孤兒：生成紀錄排隊中／執行中，或 id 還沒 commit（大於 sqlite_sequence）。

用法（在專案根目錄執行）：
  python scripts/storage_gc.py [--settings PATH] [--dry-run] [--days 30] [-v]
--dry-run 只計數、不刪任何東西；-v 列出每個物件。
設定與 app 相同（R2 憑證只來自環境變數）；R2 經過 CachedStorage 刪除，本機快取一起作廢。
結束碼：0 成功；1 有物件刪除失敗；2 設定或參數錯誤。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, text  # noqa: E402
from sqlmodel import Session, col, select, update  # noqa: E402

from app.config import SettingsError, load_settings  # noqa: E402
from app.db import init_db, make_engine, session_factory  # noqa: E402
from app.models import Book, Generation, GenStatus, Page, utcnow  # noqa: E402
from app.storage import Storage, make_storage  # noqa: E402

DEFAULT_DAYS = 30
_IN_FLIGHT = (GenStatus.QUEUED, GenStatus.RUNNING)
# books/{book}/generations/{gen}.png 或 {gen}_mask.png
_GEN_KEY = re.compile(r"^books/\d+/generations/(\d+)(?:_mask)?\.png$")
# cache/previews/{gen}_{key}.png
_PREVIEW_KEY = re.compile(r"^cache/previews/(\d+)_[^/]*$")


@dataclass
class GcReport:
    dry_run: bool
    days: int
    cutoff: datetime
    expired_generations: int = 0
    expired_objects: int = 0
    orphan_objects: int = 0
    orphan_previews: int = 0
    skipped_in_flight: int = 0
    errors: int = 0
    actions: list[tuple[str, str]] = field(default_factory=list)  # (原因, key)


@dataclass
class _State:
    seq: int  # 已 commit 的最大 generation id（AUTOINCREMENT，id 不重用）；更大的 id 是還沒 commit 的列
    gens: dict[int, tuple[GenStatus, str | None]]  # id → (status, image_path)
    referenced: set[str]
    expired: dict[int, list[str]]  # 過期的捨棄生成紀錄 id → 它的物件 key（image／mask）


def _gen_seq(s: Session) -> int:
    row = s.exec(text("SELECT seq FROM sqlite_sequence WHERE name = 'generation'")).first()
    max_id = s.exec(select(func.max(Generation.id))).one() or 0
    return max(int(row[0]) if row else 0, max_id)


def _character_refs(gens: Sequence[Generation]) -> set[str]:
    """排隊中／執行中任務的 request_json 裡的角色圖 key（worker 開始時才讀）。"""
    refs: set[str] = set()
    for g in gens:
        try:
            ref = json.loads(g.request_json).get("character_image")
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(ref, str) and ref:
            refs.add(ref)
    return refs


def _protected_gen_ids(s: Session) -> set[int]:
    ids: set[int] = set()
    for row in s.exec(select(Page.selected_generation_id, Page.background_generation_id,
                             Page.pose_generation_id)).all():
        ids.update(i for i in row if i is not None)
    ids.update(i for i in s.exec(select(Book.character_generation_id)).all() if i is not None)
    return ids


def _load_state(s: Session, now: datetime, days: int) -> _State:
    cutoff = now - timedelta(days=days)
    seq = _gen_seq(s)
    gens = s.exec(select(Generation)).all()
    referenced: set[str] = set()
    for g in gens:
        referenced.update(k for k in (g.image_path, g.mask_path) if k)
    referenced.update(p for p in s.exec(select(Page.lineart_path)).all() if p)
    char_refs = _character_refs([g for g in gens if g.status in _IN_FLIGHT])
    referenced |= char_refs
    protected = _protected_gen_ids(s)
    expired: dict[int, list[str]] = {}
    for g in gens:
        keys = [k for k in (g.image_path, g.mask_path) if k]
        if (
            g.discarded
            and g.discarded_at is not None
            and g.discarded_at < cutoff
            and keys
            and g.id not in protected
            and g.status not in _IN_FLIGHT
            and not (set(keys) & char_refs)
        ):
            expired[g.id] = keys
    return _State(
        seq=seq,
        gens={g.id: (g.status, g.image_path) for g in gens},
        referenced=referenced,
        expired=expired,
    )


def _clear_expired(s: Session, ids: Sequence[int], cutoff: datetime) -> set[int]:
    """清空過期捨棄圖的 key。條件在 UPDATE 裡重新檢查（讀完之後才被選用／還原的不清），回傳真的清掉的 id。"""
    if not ids:
        return set()
    gid = col(Generation.id)
    in_use = (
        select(Page.id).where(
            (col(Page.selected_generation_id) == gid)
            | (col(Page.background_generation_id) == gid)
            | (col(Page.pose_generation_id) == gid)
        ).exists()
        | select(Book.id).where(col(Book.character_generation_id) == gid).exists()
    )
    res = s.exec(
        update(Generation)
        .where(
            gid.in_(list(ids)),
            col(Generation.discarded).is_(True),
            col(Generation.discarded_at) < cutoff,
            col(Generation.status).not_in(_IN_FLIGHT),
            ~in_use,
        )
        .values(image_path=None, mask_path=None)
        .returning(Generation.id)
    )
    cleared = set(res.scalars().all())
    s.commit()
    return cleared


def _orphans(state: _State, book_keys: Sequence[str], preview_keys: Sequence[str],
             report: GcReport) -> tuple[list[str], list[str]]:
    """回傳 (books/ 的孤兒, 預覽孤兒)；可能正在寫入的計入 skipped_in_flight。"""
    orphans: list[str] = []
    for key in book_keys:
        if key in state.referenced:
            continue
        m = _GEN_KEY.match(key)
        if m:
            gid = int(m.group(1))
            if gid > state.seq or (gid in state.gens and state.gens[gid][0] in _IN_FLIGHT):
                report.skipped_in_flight += 1
                continue
        orphans.append(key)
    previews: list[str] = []
    for key in preview_keys:
        gid = _preview_gid(key)
        if gid is None or gid in state.expired:
            continue  # 不認得的檔名不動；過期的由過期步驟處理
        if gid > state.seq:
            report.skipped_in_flight += 1
            continue
        if _preview_is_orphan(state, gid):
            previews.append(key)
    return orphans, previews


def _preview_gid(key: str) -> int | None:
    m = _PREVIEW_KEY.match(key)
    return int(m.group(1)) if m else None


def _preview_is_orphan(state: _State, gid: int) -> bool:
    """生成紀錄已不存在，或已沒有圖（例如過期清掉）且不在排隊／執行中。"""
    gen = state.gens.get(gid)
    return gen is None or (gen[1] is None and gen[0] not in _IN_FLIGHT)


def run_gc(sessions, storage: Storage, *, days: int = DEFAULT_DAYS, dry_run: bool = False,
           now: datetime | None = None) -> GcReport:
    now = now or utcnow()
    report = GcReport(dry_run=dry_run, days=days, cutoff=now - timedelta(days=days))
    # 先列物件再讀 DB：列出之後才 commit 的列，讀 DB 時就看得到
    book_keys = list(storage.list("books/"))
    preview_keys = list(storage.list("cache/previews/"))
    with sessions() as s:
        state = _load_state(s, now, days)

    def drop(reason: str, key: str) -> bool:
        report.actions.append((reason, key))
        if dry_run:
            return True
        try:
            storage.delete(key)
            return True
        except Exception as e:  # noqa: BLE001
            report.errors += 1
            print(f"刪除失敗 {key}：{type(e).__name__}: {e}", file=sys.stderr)
            return False

    # 1. 過期的捨棄圖：先清 key（條件式），再刪物件；刪失敗的物件下次會被當成孤兒
    if dry_run:
        cleared = set(state.expired)
    else:
        with sessions() as s:
            cleared = _clear_expired(s, sorted(state.expired), report.cutoff)
    report.expired_generations = len(cleared)
    for gid in sorted(cleared):
        keys = state.expired[gid] + [k for k in preview_keys if _preview_gid(k) == gid]
        report.expired_objects += sum(drop("expired", k) for k in keys)

    # 2. 孤兒：刪之前再讀一次參照（縮短與其他行程競爭的時間）
    orphans, previews = _orphans(state, book_keys, preview_keys, report)
    if not dry_run and (orphans or previews):
        with sessions() as s:
            fresh = _load_state(s, now, days)
        orphans = [k for k in orphans if k not in fresh.referenced]
        previews = [k for k in previews if _preview_is_orphan(fresh, _preview_gid(k))]
    report.orphan_objects = sum(drop("orphan", k) for k in orphans)
    report.orphan_previews = sum(drop("orphan-preview", k) for k in previews)
    return report


def format_report(r: GcReport, *, verbose: bool = False) -> str:
    verb = "會刪除" if r.dry_run else "已刪除"
    lines = [
        "storage_gc（dry-run：只計數，不刪除任何東西）" if r.dry_run else "storage_gc",
        f"保留天數：{r.days}（discarded_at 早於 {r.cutoff.isoformat(timespec='seconds')}）",
        f"過期的捨棄生成紀錄：{r.expired_generations} 筆，{verb}物件 {r.expired_objects} 個",
        f"孤兒物件：{verb} books/ {r.orphan_objects} 個、cache/previews/ {r.orphan_previews} 個",
        f"略過（可能正在寫入）：{r.skipped_in_flight} 個",
    ]
    if r.errors:
        lines.append(f"刪除失敗：{r.errors} 個（下次執行會再試）")
    if verbose:
        lines += [f"  {reason:<15} {key}" for reason, key in r.actions]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--settings", type=Path, help="設定檔（預設 config/settings.yaml 或 APP_SETTINGS_FILE）")
    p.add_argument("--dry-run", action="store_true", help="只計數，不刪除")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"捨棄多少天後刪除（預設 {DEFAULT_DAYS}）")
    p.add_argument("-v", "--verbose", action="store_true", help="列出每個物件")
    args = p.parse_args(argv)
    if args.days < 0:
        print("--days 不可小於 0", file=sys.stderr)
        return 2
    try:
        settings = load_settings(args.settings, check_paths=False)
        db_path = settings.paths.database
        if not db_path.is_file():
            raise SettingsError([f"找不到資料庫：{db_path}"])
        engine = make_engine(db_path)
        init_db(engine)
        storage = make_storage(settings)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    report = run_gc(session_factory(engine), storage, days=args.days, dry_run=args.dry_run)
    print(format_report(report, verbose=args.verbose))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
