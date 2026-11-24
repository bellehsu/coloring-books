"""每個邀請（owner）每天的生成上限。

- 排隊時計數（`reserve`，與新增 Generation 同一個交易），任務最後 FAILED／CANCELLED 時退回（`refund_generations`）。
- 日界線是 Asia/Taipei。超過上限回 429「今天的生成次數已用完，明天再來」。
- 合成（compose）、上傳背景與自動選圖不用 GPU，不計數。
- admin 照常計數（看得到用量）但不設上限。
- 每筆計數的 generation 在 request_json 記下 `usage_day`（台北日期）與 `usage_owner`（被計數的 owner；
  admin 替別人的書排的任務算在 admin 身上），退回時用這兩個值，跨日或換人都退到原本那一列。
  退回後寫 `usage_refunded: true`，同一筆永遠只退一次。
- `auto_cleanup` 頁的 AI 背景算 2 次（背景＋自動清理，request_json 帶 `cleanup_reserved`）：整批用
  `reserve`／`reserve_prefix` 依權重預留，背景失敗／取消時退 2 次；排了清理後那 1 次的紀錄移到清理上。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, select, update

from app.models import Generation, GenStatus, Owner, Usage, utcnow
from app.services.gen_request import CLEANUP_RESERVED, USAGE_DAY, USAGE_OWNER, USAGE_REFUNDED

log = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
DAILY_CAP_REACHED = "今天的生成次數已用完，明天再來"
_REFUNDABLE = (GenStatus.FAILED, GenStatus.CANCELLED)


def clock() -> datetime:
    """目前時間（UTC）；測試以 monkeypatch 換掉。"""
    return utcnow()


def taipei_day(now: datetime) -> date:
    """now 在台北是哪一天；naive datetime 視為 UTC（與 DB 存的時間一致）。"""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(TAIPEI).date()


def reserve(session: Session, owner: Owner, n: int, now: datetime) -> date:
    """替 owner 預留今天（台北）的 n 次生成；不夠就丟 429，什麼都不寫。回傳計數的日期。

    單一條 upsert：沒有列就插入 n，有列就在 `generations + n <= cap` 時加 n（admin 不設條件）。
    SQLite 的寫入鎖讓同時的請求依序執行，所以不會超過上限，第一次插入也不會撞主鍵。
    不 commit：呼叫端在同一個交易新增 Generation 後一起 commit，失敗就一起 rollback。
    """
    if n <= 0:
        raise ValueError("n 必須是正整數")
    day = taipei_day(now)
    cap = None if owner.is_admin else owner.daily_generation_cap
    if cap is not None and n > cap:
        raise HTTPException(429, _over_cap_detail(session, owner, day, cap, n))
    table = Usage.__table__
    stmt = insert(table).values(owner_id=owner.id, day=day, generations=n)
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.owner_id, table.c.day],
        set_={"generations": table.c.generations + n},
        where=None if cap is None else (table.c.generations + n <= cap),
    )
    res = session.exec(stmt)
    if res.rowcount != 1:
        raise HTTPException(429, _over_cap_detail(session, owner, day, cap, n))
    return day


def remaining(session: Session, owner: Owner, now: datetime) -> int | None:
    """owner 今天（台北）還能排幾次；admin 不設上限回 None。只讀。"""
    if owner.is_admin:
        return None
    return max(owner.daily_generation_cap - _used(session, owner, taipei_day(now)), 0)


def reserve_up_to(session: Session, owner: Owner, n: int, now: datetime) -> tuple[date, int]:
    """「開始做這本書」用：最多預留 n 次，剩下的不夠就只預留剩下的（使用者決定：放得下多少做多少）。
    一次都不剩 → 429「今天的生成次數已用完，明天再來」。回傳 (計數的日期, 實際預留的次數)。

    先以 INSERT OR IGNORE（0 次）拿到寫入鎖，之後讀到的已用量在這個交易結束前不會被別的請求改掉，
    所以「讀剩幾次 → 預留」是原子的，同時按也不會超過上限。不 commit（同 reserve）。"""
    if n <= 0:
        raise ValueError("n 必須是正整數")
    return reserve_prefix(session, owner, [1] * n, now)


def fitting_prefix(costs: Sequence[int], left: int | None) -> int:
    """依序排、每筆 costs[i] 次：left 次（None＝不設上限）放得下前幾筆。放不下的那筆之後都不排
    （不跳過去排後面便宜的：一頁的背景不會跟它的清理分開，順序也與畫面上的頁序一致）。"""
    if left is None:
        return len(costs)
    k = total = 0
    for c in costs:
        if total + c > left:
            break
        total += c
        k += 1
    return k


def reserve_prefix(session: Session, owner: Owner, costs: Sequence[int], now: datetime) -> tuple[date, int]:
    """「開始做這本書」用：依序預留放得下的前幾筆（每筆 costs[i] 次；auto_cleanup 頁的背景是 2），
    其餘留到明天。一筆都放不下 → 429：一次都不剩是「明天再來」，還剩幾次但不夠第一筆是
    「今天只剩 X 次，這次需要 N 次」（N＝全部的次數）。回傳 (計數的日期, 排得下的筆數)。
    每筆都是 1 次時就是 reserve_up_to。原子性同 reserve_up_to（先拿寫入鎖再讀剩幾次）。不 commit。"""
    if not costs or any(c <= 0 for c in costs):
        raise ValueError("costs 必須是正整數")
    if owner.is_admin:
        return reserve(session, owner, sum(costs), now), len(costs)
    day = taipei_day(now)
    table = Usage.__table__
    session.exec(
        insert(table).values(owner_id=owner.id, day=day, generations=0)
        .on_conflict_do_nothing(index_elements=[table.c.owner_id, table.c.day])
    )
    left = max(owner.daily_generation_cap - _used(session, owner, day), 0)
    k = fitting_prefix(costs, left)
    if k == 0:
        raise HTTPException(429, f"今天只剩 {left} 次，這次需要 {sum(costs)} 次" if left > 0 else DAILY_CAP_REACHED)
    return reserve(session, owner, sum(costs[:k]), now), k


def _used(session: Session, owner: Owner, day: date) -> int:
    return session.exec(
        select(Usage.generations).where(col(Usage.owner_id) == owner.id, col(Usage.day) == day)
    ).first() or 0


def _over_cap_detail(session: Session, owner: Owner, day: date, cap: int, n: int) -> str:
    """還剩幾次但不夠這一批（使用者決定：整批擋下、說清楚）；一次都不剩就是原本的「明天再來」。"""
    left = max(cap - _used(session, owner, day), 0)
    return f"今天只剩 {left} 次，這次需要 {n} 次" if left > 0 else DAILY_CAP_REACHED


def refund(session: Session, owner_id: int, day: date, n: int = 1) -> None:
    """退回 n 次（不會低於 0；那天沒有列就什麼都不做）。不 commit。"""
    session.exec(
        update(Usage)
        .where(col(Usage.owner_id) == owner_id, col(Usage.day) == day)
        .values(generations=func.max(col(Usage.generations) - n, 0))
    )


def usage_extra(owner: Owner | int, day: date) -> dict:
    """排隊時寫進 request_json 的計數紀錄（encode_request 的 extra）；owner 可以是 owner id
    （自動清理沿用來源背景的計數紀錄）。"""
    return {USAGE_DAY: day.isoformat(), USAGE_OWNER: owner if isinstance(owner, int) else owner.id}


def counted(gen: Generation) -> tuple[int, date] | None:
    """這筆 generation 的計數紀錄 (usage_owner, usage_day)；沒有、已退過或壞掉回 None。"""
    try:
        d = json.loads(gen.request_json)
        if not isinstance(d, dict) or d.get(USAGE_REFUNDED):
            return None
        day = date.fromisoformat(d[USAGE_DAY])
        owner_id = d[USAGE_OWNER]
    except (TypeError, ValueError, KeyError):
        return None
    return (owner_id, day) if isinstance(owner_id, int) else None


def refund_generations(session: Session, gen_ids: Iterable[int]) -> int:
    """這些 generation 裡已是 FAILED／CANCELLED、有計數紀錄且還沒退過的，各退一次並標記
    （還帶著 cleanup_reserved 的背景退 2 次：它的自動清理還沒排）。

    在把狀態改成 FAILED／CANCELLED 的同一個交易裡呼叫（呼叫端 commit）；已退過、舊列（沒有
    usage_day）、request_json 壞掉的都略過，所以重複呼叫也安全。回傳退回的筆數。
    """
    ids = list(gen_ids)
    if not ids:
        return 0
    n = 0
    rows = session.exec(
        select(Generation)
        .where(col(Generation.id).in_(ids), col(Generation.status).in_(_REFUNDABLE))
        .execution_options(populate_existing=True)  # 狀態剛被批次 UPDATE 改過，不用 session 裡的舊值
    ).all()
    for gen in rows:
        found = counted(gen)
        if found is None:
            continue
        owner_id, day = found
        d = json.loads(gen.request_json)
        refund(session, owner_id, day, 2 if d.get(CLEANUP_RESERVED) is True else 1)
        d[USAGE_REFUNDED] = True
        gen.request_json = json.dumps(d, ensure_ascii=False, sort_keys=True)
        session.add(gen)
        n += 1
    session.flush()
    return n
