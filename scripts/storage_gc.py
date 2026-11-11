#!/usr/bin/env python3
"""清理儲存空間（排程執行；排程方式由雲端階段決定）。

1. 過期的捨棄圖：`discarded_at` 早於 --days 天（預設 30）的生成紀錄，刪掉它的圖片、遮罩與預覽物件，
   並清空 image_path／mask_path。頁面選用中（selected／background／pose）、書的角色、
   排隊中任務要用的角色圖，捨棄再久也不刪。
2. 孤兒物件：books/ 底下沒有任何資料列指到的物件（生成圖、遮罩、頁面線稿 lineart_path），
   以及 cache/previews/ 底下生成紀錄已不存在（或已捨棄且圖已清掉）的預覽。
   正在寫入的物件不算孤兒：生成紀錄排隊中／執行中，或 id 還沒 commit（大於 sqlite_sequence）。
   與 DB 對不上的物件也不刪：書號大於 DB 最大的書、頁／生成紀錄 id 超出 DB，或該 id 的列屬於另一本書。

用法（在專案根目錄執行）：
  python scripts/storage_gc.py [--settings PATH] [--dry-run] [--days 30] [-v] [--allow-mass-delete]
--dry-run 只計數、不刪任何東西；-v 列出每個物件。
設定與 app 相同（R2 憑證只來自環境變數）；R2 經過 CachedStorage 刪除，本機快取一起作廢。
孤兒超過 20 個且超過「孤兒＋被參照物件」的 20%，或與 DB 對不上的物件超過 20 個且超過列出物件的 20%
（多半是 DB 與 storage 不是一組）時整個中止、什麼都不刪，除非加 --allow-mass-delete。資料庫結構不是最新版時拒絕執行（不跑 migration；請先啟動後端）。
結束碼：0 成功；1 有物件刪除失敗；2 設定或參數錯誤；3 孤兒太多而中止。
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
from app.db import make_engine, schema_revisions, session_factory  # noqa: E402
from app.models import Book, Generation, GenStatus, Page, utcnow  # noqa: E402
from app.storage import Storage, make_storage  # noqa: E402

DEFAULT_DAYS = 30
_IN_FLIGHT = (GenStatus.QUEUED, GenStatus.RUNNING)
_BOOK_KEY = re.compile(r"^books/(\d+)/")
# books/{book}/generations/{gen}.png 或 {gen}_mask.png
_GEN_KEY = re.compile(r"^books/\d+/generations/(\d+)(?:_mask)?\.png$")
# books/{book}/pages/{page}/…
_PAGE_KEY = re.compile(r"^books/\d+/pages/(\d+)/")
# 孤兒同時超過這兩個門檻就整個中止（多半是 DB 與 storage 對不上），除非 --allow-mass-delete
MASS_DELETE_RATIO = 0.2
MASS_DELETE_MIN = 20
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
    skipped_mismatched: int = 0  # 物件與 DB 對不上（書／頁 id 超出 DB，或屬於另一本書）：不刪
    aborted: bool = False  # 孤兒太多，什麼都沒刪
    mass_candidates: int = 0  # 孤兒（books/＋預覽）
    abort_reason: str = ""
    referenced: int = 0  # 列出的物件裡有資料列指到的
    listed: int = 0
    errors: int = 0
    actions: list[tuple[str, str]] = field(default_factory=list)  # (原因, key)


@dataclass
class _State:
    seq: int  # 已 commit 的最大 generation id（AUTOINCREMENT，id 不重用）；更大的 id 是還沒 commit 的列
    max_book: int
    page_seq: int
    gens: dict[int, tuple[GenStatus, str | None, int, bool]]  # id → (status, image_path, book_id, discarded)
    pages: dict[int, int]  # id → book_id
    referenced: set[str]
    expired: dict[int, list[str]]  # 過期的捨棄生成紀錄 id → 它的物件 key（image／mask）


def _seq(s: Session, model) -> int:
    """AUTOINCREMENT 表已 commit 的最大 id（含已刪除的列）。"""
    row = s.exec(text("SELECT seq FROM sqlite_sequence WHERE name = :t").bindparams(t=model.__tablename__)).first()
    max_id = s.exec(select(func.max(model.id))).one() or 0
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
    seq = _seq(s, Generation)
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
        max_book=s.exec(select(func.max(Book.id))).one() or 0,
        page_seq=_seq(s, Page),
        gens={g.id: (g.status, g.image_path, g.book_id, g.discarded) for g in gens},
        pages={pid: bid for pid, bid in s.exec(select(Page.id, Page.book_id)).all()},
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


def _classify_book_key(state: _State, key: str) -> str:
    """books/ 底下沒有被參照的 key：orphan、in_flight（可能正在寫入）或 mismatched（與 DB 對不上，不刪）。"""
    m = _BOOK_KEY.match(key)
    if not m or int(m.group(1)) > state.max_book:
        return "mismatched"  # 書號超出 DB：多半是別的 DB 的物件
    book_id = int(m.group(1))
    m = _GEN_KEY.match(key)
    if m:
        gid = int(m.group(1))
        gen = state.gens.get(gid)
        if gid > state.seq or (gen is not None and gen[0] in _IN_FLIGHT):
            return "in_flight"
        if gen is not None and gen[2] != book_id:
            return "mismatched"  # 這個 id 的生成紀錄屬於另一本書
        return "orphan"
    m = _PAGE_KEY.match(key)
    if m:
        pid = int(m.group(1))
        if pid > state.page_seq or (pid in state.pages and state.pages[pid] != book_id):
            return "mismatched"
    return "orphan"


def _orphans(state: _State, book_keys: Sequence[str], preview_keys: Sequence[str],
             report: GcReport) -> tuple[list[str], list[str]]:
    """回傳 (books/ 的孤兒, 預覽孤兒)；略過的分別計入 skipped_in_flight／skipped_mismatched。"""
    orphans: list[str] = []
    for key in book_keys:
        if key in state.referenced:
            report.referenced += 1
            continue
        kind = _classify_book_key(state, key)
        if kind == "in_flight":
            report.skipped_in_flight += 1
        elif kind == "mismatched":
            report.skipped_mismatched += 1
        else:
            orphans.append(key)
    previews: list[str] = []
    for key in preview_keys:
        gid = _preview_gid(key)
        if gid is None:
            continue  # 不認得的檔名：不動
        if gid in state.expired:
            report.referenced += 1  # 由過期步驟處理
            continue
        if gid > state.seq:
            report.skipped_in_flight += 1
            continue
        if _preview_is_orphan(state, gid):
            previews.append(key)
        else:
            report.referenced += 1
    report.mass_candidates = len(orphans) + len(previews)
    return orphans, previews


def _preview_gid(key: str) -> int | None:
    m = _PREVIEW_KEY.match(key)
    return int(m.group(1)) if m else None


def _preview_is_orphan(state: _State, gid: int) -> bool:
    """生成紀錄已不存在（id 不超過 sqlite_sequence），或是已被 GC 清掉圖的捨棄紀錄（例如清掉後預覽刪失敗）。
    只認「捨棄＋沒有圖」：預覽 key 沒有書號，對不上的 DB 裡同 id、沒有圖的列不能讓活的預覽被刪。"""
    if gid > state.seq:
        return False
    gen = state.gens.get(gid)
    return gen is None or (gen[3] and gen[1] is None and gen[0] not in _IN_FLIGHT)


def _over(n: int, total: int) -> bool:
    return n > MASS_DELETE_MIN and n > MASS_DELETE_RATIO * total


def _mass_delete_reason(r: GcReport) -> str | None:
    """DB 與 storage 看起來不是一組時回傳原因（整個中止）；正常回 None。
    - 孤兒佔「孤兒＋被參照」超過門檻（分母不含略過的物件，對不上的再多也稀釋不了）；
    - 對不上的物件佔全部列出的超過門檻（正常的 DB 幾乎沒有對不上的物件）。"""
    if _over(r.mass_candidates, r.mass_candidates + r.referenced):
        return (f"{r.mass_candidates} 個孤兒物件（被參照的只有 {r.referenced} 個；"
                f"超過 {MASS_DELETE_MIN} 個且超過 {MASS_DELETE_RATIO:.0%}）")
    if _over(r.skipped_mismatched, r.listed):
        return (f"{r.skipped_mismatched} 個物件與資料庫對不上（列出 {r.listed} 個；"
                f"超過 {MASS_DELETE_MIN} 個且超過 {MASS_DELETE_RATIO:.0%}）")
    return None


def run_gc(sessions, storage: Storage, *, days: int = DEFAULT_DAYS, dry_run: bool = False,
           now: datetime | None = None, allow_mass_delete: bool = False) -> GcReport:
    now = now or utcnow()
    report = GcReport(dry_run=dry_run, days=days, cutoff=now - timedelta(days=days))
    # 先列物件再讀 DB：列出之後才 commit 的列，讀 DB 時就看得到
    book_keys = list(storage.list("books/"))
    preview_keys = list(storage.list("cache/previews/"))
    report.listed = len(book_keys) + len(preview_keys)
    with sessions() as s:
        state = _load_state(s, now, days)

    # 孤兒太多多半是 DB 與 storage 對不上（設定指錯 DB／bucket）：整個中止，連過期的也不刪
    orphans, previews = _orphans(state, book_keys, preview_keys, report)
    reason = None if allow_mass_delete else _mass_delete_reason(report)
    if reason:
        report.aborted = True
        report.abort_reason = reason
        return report

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
    if not dry_run and (orphans or previews):
        with sessions() as s:
            fresh = _load_state(s, now, days)
        orphans = [k for k in orphans if k not in fresh.referenced and _classify_book_key(fresh, k) == "orphan"]
        previews = [k for k in previews if _preview_is_orphan(fresh, _preview_gid(k))]
    report.orphan_objects = sum(drop("orphan", k) for k in orphans)
    report.orphan_previews = sum(drop("orphan-preview", k) for k in previews)
    return report


def format_report(r: GcReport, *, verbose: bool = False) -> str:
    if r.aborted:
        return "\n".join([
            f"storage_gc 已中止，什麼都沒刪：{r.abort_reason}。",
            "多半是設定的資料庫與 storage（資料夾／bucket）對不上；請先確認 paths.database 與 storage 設定。",
            "確定要刪就加 --allow-mass-delete（建議先用 --dry-run -v --allow-mass-delete 看清單）。",
        ])
    verb = "會刪除" if r.dry_run else "已刪除"
    lines = [
        "storage_gc（dry-run：只計數，不刪除任何東西）" if r.dry_run else "storage_gc",
        f"保留天數：{r.days}（discarded_at 早於 {r.cutoff.isoformat(timespec='seconds')}）",
        f"過期的捨棄生成紀錄：{r.expired_generations} 筆，{verb}物件 {r.expired_objects} 個",
        f"孤兒物件：{verb} books/ {r.orphan_objects} 個、cache/previews/ {r.orphan_previews} 個",
        f"略過（可能正在寫入）：{r.skipped_in_flight} 個",
        f"略過（與資料庫對不上）：{r.skipped_mismatched} 個",
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
    p.add_argument("--allow-mass-delete", action="store_true",
                   help="孤兒或對不上的物件太多時仍然執行（預設整個中止）；對不上的物件照樣不刪")
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
        # 不在這裡升級資料庫結構（與執行中的後端搶著跑 migration 很危險）：不是最新版就請使用者先啟動後端
        current, head = schema_revisions(engine)
        if current != head:
            raise SettingsError([
                f"資料庫結構不是最新版（目前 {current or '無'}，最新 {head}）：{db_path}。"
                "請先啟動一次後端（會自動升級），再執行 storage_gc。"
            ])
        storage = make_storage(settings)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    report = run_gc(session_factory(engine), storage, days=args.days, dry_run=args.dry_run,
                    allow_mass_delete=args.allow_mass_delete)
    if report.aborted:
        print(format_report(report), file=sys.stderr)
        return 3
    print(format_report(report, verbose=args.verbose))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
