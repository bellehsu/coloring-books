#!/usr/bin/env python3
"""清理儲存空間（排程執行；排程方式由雲端階段決定）。

1. 過期的捨棄圖：`discarded_at` 早於 --days 天（預設 30）的生成紀錄，刪掉它的圖片與遮罩物件，
   並清空 image_path／mask_path。頁面選用中（selected／background／pose／auto_*）、復原快照用到的、有進行中頁面任務的頁面、書的角色、
   排隊中任務要用的角色圖，捨棄再久也不刪。
2. 舊合成圖：每次拖位置／換背景／復原都會新增一筆 composite generation，沒有流程會捨棄它。
   不是任何頁面的選定圖、建立時間早於 7 天（固定值，不受 --days 影響）、而且不是該頁最新 3 筆（依 id）的 composite，
   刪掉它的圖片與遮罩物件並清空 image_path／mask_path（列留著）。有進行中頁面任務的頁面、排隊中任務要用的角色圖不動。
3. 孤兒物件：books/ 底下沒有任何資料列指到的物件（生成圖、遮罩、頁面線稿 lineart_path），
   另外一次性清掉 cache/previews/ 底下全部的舊預覽物件（預覽功能已移除，之後沒有新的；這條規則之後可以拿掉）。
   正在寫入的物件不算孤兒：生成紀錄排隊中／執行中，或 id 還沒 commit（大於 sqlite_sequence）。
   與 DB 對不上的物件也不刪：書號大於 DB 最大的書、頁／生成紀錄 id 超出 DB，或該 id 的列屬於另一本書。

用法（在專案根目錄執行）：
  python scripts/storage_gc.py [--settings PATH] [--dry-run] [--days 30] [-v] [--allow-mass-delete]
  python scripts/storage_gc.py --init-instance [--adopt-bucket-id] [--yes]
--dry-run 只計數、不刪任何東西；-v 列出每個物件。
設定與 app 相同（R2 憑證只來自環境變數）；R2 經過 CachedStorage 刪除，本機快取一起作廢。
孤兒超過 20 個且超過「孤兒＋被參照物件」的 20%，或與 DB 對不上的物件超過 20 個且超過列出物件的 20%
（多半是 DB 與 storage 不是一組），或該清的舊合成圖超過有圖的合成圖的 50% 時整個中止、什麼都不刪，
除非加 --allow-mass-delete。資料庫結構不是最新版時拒絕執行（不跑 migration；請先啟動後端）。
執行前要求 DB 的 storage_instance_id 與 storage 的 books/_instance.json 一致（後端啟動時建立），
否則結束碼 4、什麼都不刪（--allow-mass-delete 也不例外）。缺標記時，確認設定無誤後用 --init-instance 補上：
DB 有 id、storage 沒有 → 寫進 storage；兩邊都沒有 → 建立新 id；DB 沒有、storage 有 → 加 --adopt-bucket-id
把 storage 的 id 寫進 DB。都要輸入 storage 名稱（R2 bucket 名稱／本機資料夾路徑）確認，--yes 略過；永不覆寫已存在的 id。
結束碼：0 成功；1 有物件刪除失敗（--init-instance：取消）；2 設定或參數錯誤；3 孤兒（或舊合成圖）太多而中止；
4 DB 與 storage 不是一組；5 讀不到 storage 的身分標記（例如 R2 403、逾時）。
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
from app.models import Book, Generation, GenKind, GenStatus, Page, PageHistory, PageTask, PageTaskStatus, utcnow  # noqa: E402
from app.services import instance  # noqa: E402
from app.storage import Storage, make_storage  # noqa: E402

DEFAULT_DAYS = 30
COMPOSITE_KEEP_DAYS = 7  # 合成圖至少保留這麼久
COMPOSITE_KEEP_NEWEST = 3  # 每頁最新幾筆合成圖一律保留
_IN_FLIGHT = (GenStatus.QUEUED, GenStatus.RUNNING)
_BOOK_KEY = re.compile(r"^books/(\d+)/")
# books/{book}/generations/{gen}.png 或 {gen}_mask.png
_GEN_KEY = re.compile(r"^books/\d+/generations/(\d+)(?:_mask)?\.png$")
# books/{book}/pages/{page}/…
_PAGE_KEY = re.compile(r"^books/\d+/pages/(\d+)/")
# 孤兒同時超過這兩個門檻就整個中止（多半是 DB 與 storage 對不上），除非 --allow-mass-delete
MASS_DELETE_RATIO = 0.2
MASS_DELETE_MIN = 20
# 該清的舊合成圖超過「有圖的合成圖」的這個比例就整個中止（例如時鐘錯了），除非 --allow-mass-delete
COMPOSITE_DELETE_RATIO = 0.5


InstanceMismatch = instance.InstanceMismatch  # DB 與 storage 不是一組：什麼都不能刪（結束碼 4）
InstanceUnavailable = instance.InstanceUnavailable  # 讀不到 storage 的身分標記（結束碼 5）


def check_instance(sessions, storage: Storage) -> instance.InstanceCheck:
    """執行前確認 DB 與 storage 是一組；否則丟 InstanceMismatch（訊息列出兩邊的 id 與下一步）。"""
    with sessions() as s:
        return instance.require_instance(s, storage, tool="storage_gc")


@dataclass
class GcReport:
    dry_run: bool
    days: int
    cutoff: datetime
    expired_generations: int = 0
    expired_objects: int = 0
    expired_composites: int = 0
    composite_objects: int = 0
    orphan_objects: int = 0
    orphan_previews: int = 0
    skipped_in_flight: int = 0
    skipped_mismatched: int = 0  # 物件與 DB 對不上（書／頁 id 超出 DB，或屬於另一本書）：不刪
    aborted: bool = False  # 孤兒太多，什麼都沒刪
    mass_candidates: int = 0  # 孤兒（books/）
    composite_candidates: int = 0  # 該清的舊合成圖（它們的物件已算在 referenced 裡；不算進孤兒比例）
    composite_total: int = 0  # 有圖（image_path）的合成圖
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
    composites: dict[int, list[str]]  # 該清的舊合成圖 id → 它的物件 key
    composite_total: int  # 有圖（image_path）的合成圖數


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


# 復原快照（history.snapshot_of）只固定背景與角色動作；不固定合成圖（composite），所以合成圖不受快照保護
_SNAPSHOT_GEN_FIELDS = ("background_generation_id", "pose_generation_id")


def _snapshot_gen_ids(snapshot_json: str) -> set[int]:
    """復原快照裡的生成紀錄 id（筆畫、位置、文字都存在 DB／JSON 裡，不指到 storage 物件）。"""
    try:
        snap = json.loads(snapshot_json)
    except (TypeError, ValueError):
        return set()
    if not isinstance(snap, dict):
        return set()
    return {v for k in _SNAPSHOT_GEN_FIELDS if isinstance((v := snap.get(k)), int) and not isinstance(v, bool)}


def _protected_gen_ids(s: Session) -> set[int]:
    """捨棄再久也不刪的生成紀錄：選用中、自動選的（auto_*）、書的角色、復原快照裡用到的，
    以及有排隊中／執行中頁面任務的頁面的全部生成紀錄（流程正在選圖／合成）。"""
    ids: set[int] = set()
    for row in s.exec(select(Page.selected_generation_id, Page.background_generation_id,
                             Page.pose_generation_id, Page.auto_background_generation_id,
                             Page.auto_pose_generation_id)).all():
        ids.update(i for i in row if i is not None)
    ids.update(i for i in s.exec(select(Book.character_generation_id)).all() if i is not None)
    for snap in s.exec(select(PageHistory.snapshot_json)).all():
        ids |= _snapshot_gen_ids(snap)
    active_pages = select(PageTask.page_id).where(
        col(PageTask.status).in_([PageTaskStatus.QUEUED.value, PageTaskStatus.RUNNING.value]))
    ids.update(s.exec(select(Generation.id).where(col(Generation.page_id).in_(active_pages))).all())
    return ids


def _old_composites(s: Session, now: datetime, in_flight_refs: set[str]) -> dict[int, list[str]]:
    """該清掉圖片的舊合成圖：不是任何頁面的選定圖、建立超過 COMPOSITE_KEEP_DAYS 天、不是該頁最新
    COMPOSITE_KEEP_NEWEST 筆（依 id）、頁面沒有進行中的任務、不是排隊中任務要用的圖。"""
    cutoff = now - timedelta(days=COMPOSITE_KEEP_DAYS)
    selected = {i for i in s.exec(select(Page.selected_generation_id)).all() if i is not None}
    active_pages = set(s.exec(select(PageTask.page_id).where(
        col(PageTask.status).in_([PageTaskStatus.QUEUED.value, PageTaskStatus.RUNNING.value]))).all())
    by_page: dict[int, list[Generation]] = {}
    for g in s.exec(select(Generation).where(Generation.kind == GenKind.COMPOSITE,
                                             col(Generation.page_id).is_not(None)).order_by(col(Generation.id).desc())):
        by_page.setdefault(g.page_id, []).append(g)
    out: dict[int, list[str]] = {}
    for page_id, gens in by_page.items():
        if page_id in active_pages:
            continue
        for g in gens[COMPOSITE_KEEP_NEWEST:]:
            keys = [k for k in (g.image_path, g.mask_path) if k]
            if (keys and g.id not in selected and g.created_at < cutoff and g.status not in _IN_FLIGHT
                    and not (set(keys) & in_flight_refs)):
                out[g.id] = keys
    return out


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
    composites = _old_composites(s, now, char_refs)
    return _State(
        seq=seq,
        max_book=s.exec(select(func.max(Book.id))).one() or 0,
        page_seq=_seq(s, Page),
        gens={g.id: (g.status, g.image_path, g.book_id, g.discarded) for g in gens},
        pages={pid: bid for pid, bid in s.exec(select(Page.id, Page.book_id)).all()},
        referenced=referenced,
        expired=expired,
        composites=composites,
        composite_total=sum(1 for g in gens if g.kind == GenKind.COMPOSITE and g.image_path),
    )


def _clear_expired(s: Session, ids: Sequence[int], cutoff: datetime) -> set[int]:
    """清空過期捨棄圖的 key。條件在 UPDATE 裡重新檢查（讀完之後才被選用／還原的不清），回傳真的清掉的 id。"""
    if not ids:
        return set()
    protected = _protected_gen_ids(s)
    ids = [i for i in ids if i not in protected]  # 讀完之後才被快照／任務用到的也不清
    if not ids:
        return set()
    gid = col(Generation.id)
    in_use = (
        select(Page.id).where(
            (col(Page.selected_generation_id) == gid)
            | (col(Page.background_generation_id) == gid)
            | (col(Page.pose_generation_id) == gid)
            | (col(Page.auto_background_generation_id) == gid)
            | (col(Page.auto_pose_generation_id) == gid)
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


def _clear_composites(s: Session, planned: Sequence[int], now: datetime) -> set[int]:
    """清空舊合成圖的 key。條件重算一次（讀完之後才被選用、或被更新的合成圖擠進最新 3 筆的不清），回傳真的清掉的 id。"""
    if not planned:
        return set()
    char_refs = _character_refs(s.exec(select(Generation).where(col(Generation.status).in_(_IN_FLIGHT))).all())
    ids = sorted(set(planned) & set(_old_composites(s, now, char_refs)))
    if not ids:
        return set()
    # UPDATE 本身再檢查一次：重算之後才被選用的不清（與 page_compose 的選用互斥：它要求 image_path 不是空的）
    selected = select(Page.selected_generation_id).where(col(Page.selected_generation_id).is_not(None))
    res = s.exec(update(Generation)
                 .where(col(Generation.id).in_(ids), col(Generation.kind) == GenKind.COMPOSITE,
                        col(Generation.image_path).is_not(None), col(Generation.id).not_in(selected))
                 .values(image_path=None, mask_path=None)
                 .returning(Generation.id))
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


def _orphans(state: _State, book_keys: Sequence[str], report: GcReport) -> list[str]:
    """回傳 books/ 的孤兒；略過的分別計入 skipped_in_flight／skipped_mismatched。"""
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
    report.mass_candidates = len(orphans)
    report.composite_candidates = len(state.composites)
    report.composite_total = state.composite_total
    return orphans


def _over(n: int, total: int) -> bool:
    return n > MASS_DELETE_MIN and n > MASS_DELETE_RATIO * total


def _mass_delete_reason(r: GcReport) -> str | None:
    """看起來會刪錯（多半是 DB 與 storage 不是一組）時回傳原因（整個中止）；正常回 None。
    - 孤兒佔「孤兒＋被參照」超過門檻（分母不含略過的物件，對不上的再多也稀釋不了）；
    - 對不上的物件佔全部列出的超過門檻（正常的 DB 幾乎沒有對不上的物件）；
    - 該清的舊合成圖超過有圖的合成圖的一半（舊合成圖的物件有列指到，不算進孤兒比例）。"""
    if _over(r.mass_candidates, r.mass_candidates + r.referenced):
        return (f"{r.mass_candidates} 個孤兒物件（被參照的只有 {r.referenced} 個；"
                f"超過 {MASS_DELETE_MIN} 個且超過 {MASS_DELETE_RATIO:.0%}）")
    if _over(r.skipped_mismatched, r.listed):
        return (f"{r.skipped_mismatched} 個物件與資料庫對不上（列出 {r.listed} 個；"
                f"超過 {MASS_DELETE_MIN} 個且超過 {MASS_DELETE_RATIO:.0%}）")
    if r.composite_candidates > COMPOSITE_DELETE_RATIO * r.composite_total:
        return (f"{r.composite_candidates} 筆舊合成圖要清（有圖的合成圖共 {r.composite_total} 筆；"
                f"超過 {COMPOSITE_DELETE_RATIO:.0%}）")
    return None


def run_gc(sessions, storage: Storage, *, days: int = DEFAULT_DAYS, dry_run: bool = False,
           now: datetime | None = None, allow_mass_delete: bool = False) -> GcReport:
    now = now or utcnow()
    check_instance(sessions, storage)  # DB 與 storage 不是一組就什麼都不做（--allow-mass-delete 也不例外）
    report = GcReport(dry_run=dry_run, days=days, cutoff=now - timedelta(days=days))
    # 先列物件再讀 DB：列出之後才 commit 的列，讀 DB 時就看得到。身分標記永遠不列入（不能被刪）
    book_keys = [k for k in storage.list("books/") if k != instance.MARKER_KEY]
    preview_keys = list(storage.list("cache/previews/"))  # 一次性清理舊預覽：不算進孤兒比例，也不看 DB
    report.listed = len(book_keys)
    with sessions() as s:
        state = _load_state(s, now, days)

    # 孤兒太多多半是 DB 與 storage 對不上（設定指錯 DB／bucket）：整個中止，連過期的也不刪
    orphans = _orphans(state, book_keys, report)
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
        report.expired_objects += sum(drop("expired", k) for k in state.expired[gid])

    # 2. 舊合成圖：同樣先清 key（重算條件）再刪物件
    if dry_run:
        cleared_c = set(state.composites)
    else:
        with sessions() as s:
            cleared_c = _clear_composites(s, sorted(state.composites), now)
    report.expired_composites = len(cleared_c)
    for gid in sorted(cleared_c):
        report.composite_objects += sum(drop("old-composite", k) for k in state.composites[gid])

    # 3. 孤兒：刪之前再讀一次參照（縮短與其他行程競爭的時間）
    if not dry_run and orphans:
        with sessions() as s:
            fresh = _load_state(s, now, days)
        orphans = [k for k in orphans if k not in fresh.referenced and _classify_book_key(fresh, k) == "orphan"]
    report.orphan_objects = sum(drop("orphan", k) for k in orphans)
    # 一次性清理：cache/previews/ 底下的舊預覽物件一律刪（沒有程式再寫它）；之後可以拿掉這條規則
    report.orphan_previews = sum(drop("orphan-preview", k) for k in preview_keys)
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
        f"舊合成圖：{r.expired_composites} 筆，{verb}物件 {r.composite_objects} 個",
        f"孤兒物件：{verb} books/ {r.orphan_objects} 個、cache/previews/ {r.orphan_previews} 個",
        f"略過（可能正在寫入）：{r.skipped_in_flight} 個",
        f"略過（與資料庫對不上）：{r.skipped_mismatched} 個",
    ]
    if r.errors:
        lines.append(f"刪除失敗：{r.errors} 個（下次執行會再試）")
    if verbose:
        lines += [f"  {reason:<15} {key}" for reason, key in r.actions]
    return "\n".join(lines)


def _confirm(storage: Storage, yes: bool) -> bool:
    """要求輸入 storage 名稱（R2 bucket 名稱，本機為資料夾路徑）確認；--yes 略過。"""
    if yes:
        return True
    label = instance.storage_label(storage)
    try:
        answer = input(f"確認資料庫與這個 storage 是同一組。輸入 storage 名稱「{label}」確認：")
    except EOFError:  # 沒有互動式輸入：視同取消
        answer = ""
    if answer.strip() != label:
        print("名稱不符，已取消，什麼都沒寫。", file=sys.stderr)
        return False
    return True


def init_instance(sessions, storage: Storage, *, adopt_bucket_id: bool = False, yes: bool = False) -> int:
    """補上身分標記（永不覆寫任何一邊已存在的 id）。結束碼：0 一致、1 取消、4 不能寫。

    - 資料庫有 id、storage 沒有：把資料庫的 id 寫進 storage。
    - 兩邊都沒有：產生新 id，寫進資料庫與 storage（後端啟動時不自動建立的情況，例如空的新 DB＋既有 bucket）。
    - 資料庫沒有、storage 有：只有加 --adopt-bucket-id 才把 storage 的 id 寫進資料庫。
    每種寫入都要輸入 storage 名稱確認（--yes 略過）。"""
    try:
        with sessions() as s:
            res = instance.check_instance(s, storage)
            has_books = instance.db_has_books(s)
        has_objects = instance.storage_has_objects(storage)
    except instance.InstanceMarkerError as e:
        print(f"{e}；不覆寫，請手動檢查。", file=sys.stderr)
        return 4
    if res.ok:
        print(f"身分標記已一致：{res.db_id}")
        return 0
    if res.db_id and res.bucket_id:
        print(f"兩邊的身分標記不同，不覆寫任何一邊。\n{res.describe()}\n{instance.mismatch_hint(res)}", file=sys.stderr)
        return 4
    if adopt_bucket_id and res.bucket_id is None:
        print(f"--adopt-bucket-id 只用在資料庫沒有 id、storage 有標記時；什麼都沒寫。\n{res.describe()}", file=sys.stderr)
        return 4
    if res.db_id is None and res.bucket_id and not adopt_bucket_id:
        print(f"不寫入身分標記。\n{res.describe()}\n{instance.mismatch_hint(res)}", file=sys.stderr)
        return 4

    if res.db_id is None and res.bucket_id:
        plan = f"把 storage 的 id {res.bucket_id} 寫進資料庫"
    elif res.db_id:
        plan = f"把資料庫的 id {res.db_id} 寫進 storage 的 {instance.MARKER_KEY}"
    else:
        plan = f"產生新的 id，寫進資料庫與 storage 的 {instance.MARKER_KEY}"
    print(res.describe())
    print(f"資料庫{'有' if has_books else '沒有任何'}書；storage「{instance.storage_label(storage)}」的 books/ "
          f"{'已經有物件' if has_objects else '是空的'}。")
    print(f"將{plan}。")
    if not _confirm(storage, yes):
        return 1
    with sessions() as s:
        if res.db_id is None and res.bucket_id:
            after = instance.adopt_bucket_instance_id(s, storage)
        elif res.db_id:
            if instance.bucket_instance_id(storage) is None:  # 確認期間別人寫了就不覆寫
                instance.write_bucket_instance_id(storage, res.db_id)
            after = instance.check_instance(s, storage)
        else:
            after = instance.create_instance(s, storage)
    if not after.ok:
        print(f"寫完之後兩邊仍不一致（可能有別的行程同時寫入）。\n{after.describe()}", file=sys.stderr)
        return 4
    print(f"已完成：{plan}。現在兩邊都是 {after.db_id}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--settings", type=Path, help="設定檔（預設 config/settings.yaml 或 APP_SETTINGS_FILE）")
    p.add_argument("--dry-run", action="store_true", help="只計數，不刪除")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"捨棄多少天後刪除（預設 {DEFAULT_DAYS}）")
    p.add_argument("-v", "--verbose", action="store_true", help="列出每個物件")
    p.add_argument("--init-instance", action="store_true",
                   help="補上缺少的身分標記後結束（資料庫的 id 寫進 storage，或兩邊都沒有時建立新的）；"
                        "要輸入 storage 名稱確認，永不覆寫已存在的 id")
    p.add_argument("--adopt-bucket-id", action="store_true",
                   help="搭配 --init-instance：資料庫沒有 id、storage 有標記時，把 storage 的 id 寫進資料庫")
    p.add_argument("--yes", action="store_true", help="搭配 --init-instance：不詢問確認")
    p.add_argument("--allow-mass-delete", action="store_true",
                   help="孤兒或對不上的物件太多時仍然執行（預設整個中止）；對不上的物件照樣不刪")
    args = p.parse_args(argv)
    if args.days < 0:
        print("--days 不可小於 0", file=sys.stderr)
        return 2
    if args.init_instance and args.dry_run:
        print("--init-instance 會寫入身分標記，不能搭配 --dry-run", file=sys.stderr)
        return 2
    if args.adopt_bucket_id and not args.init_instance:
        print("--adopt-bucket-id 只能搭配 --init-instance", file=sys.stderr)
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
    sessions = session_factory(engine)
    try:
        if args.init_instance:
            return init_instance(sessions, storage, adopt_bucket_id=args.adopt_bucket_id, yes=args.yes)
        report = run_gc(sessions, storage, days=args.days, dry_run=args.dry_run,
                        allow_mass_delete=args.allow_mass_delete)
    except InstanceMismatch as e:
        print(e, file=sys.stderr)
        return 4
    except InstanceUnavailable as e:
        print(f"{e}\n無法確認資料庫與 storage 是同一組，什麼都沒刪。請檢查 storage 設定、憑證與網路後再試。", file=sys.stderr)
        return 5
    if report.aborted:
        print(format_report(report), file=sys.stderr)
        return 3
    print(format_report(report, verbose=args.verbose))
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
