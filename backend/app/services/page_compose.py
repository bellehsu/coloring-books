"""頁面合成（確認合成與頁面流程共用）：以頁面目前的背景、角色動作與位置產生合成圖並設為選定圖。

合成圖是一筆 `composite` generation；完全相同的既有合成圖直接選回（修線一起回來）。
肖像（位置有 frame，放進框裡）：先在背景上找框的內圈，角色只貼在內圈裡；autofit 時位置換成算出的肖像位置
並寫回頁面；找不到框就照一般合成（見 portrait_placement）。
storage I/O 一律在寫入交易外（R2 的 put 可能很慢）：先以 RUNNING、沒有圖提交暫存列，交易外寫物件，
再以條件式 UPDATE 標成成功並選用（見 store_outside_tx）。
"""

from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import replace

import numpy as np
from PIL import Image
from sqlalchemy import and_, case, literal, null
from sqlmodel import Session, col, delete, select, update

from app.models import Generation, GenKind, GenStatus, Page, PageStatus, utcnow
from app.services.compose import COMPOSE_VERSION, DEGENERATE_MASK, Placement, compose, cutout, page_placement
from app.services.frame_fit import find_interior, fit_portrait
from app.storage import Storage, StorageNotFound

log = logging.getLogger(__name__)

NEED_BACKGROUND = "請先選好背景"
NEED_POSE = "請先選好角色動作"
FILE_MISSING = "圖片檔案遺失，請重新產生"
INPUTS_CHANGED = "背景、角色動作或位置剛被更改，請重新確認合成"


def composite_matches(gen: Generation, bg_id: int, pose_id: int, placement: Placement) -> bool:
    """gen（合成圖 generation）是否正是以這組背景＋角色動作＋位置（Placement.key()）
    用目前版本（COMPOSE_VERSION）的演算法合成的。只看紀錄，不檢查檔案或狀態。"""
    if gen.kind != GenKind.COMPOSITE or gen.workflow_version != COMPOSE_VERSION:
        return False
    try:
        p = json.loads(gen.params_json or "")
        return (p["background_generation_id"] == bg_id and p["pose_generation_id"] == pose_id
                and Placement.from_dict(p["placement"]).key() == placement.key())
    except (ValueError, KeyError, TypeError):
        return False


class ComposeError(Exception):
    """無法合成（中文訊息）；API 回 409，頁面流程記成「請看一下」。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def reset_review_when(cond) -> dict:
    """條件式 UPDATE 用的 SET 片段：cond 成立（以寫入當下的舊值判斷）時退回草稿，
    角色檢查、通過時間與強制通過原因作廢；不成立時保持原值。"""
    return {
        "status": case(
            (cond, literal(PageStatus.DRAFT, Page.__table__.c.status.type)),
            else_=col(Page.status),
        ),
        "passed_at": case((cond, null()), else_=col(Page.passed_at)),
        "character_check": case((cond, null()), else_=col(Page.character_check)),
        "forced_pass_reason": case((cond, null()), else_=col(Page.forced_pass_reason)),
    }


# 背景／角色動作那一格 → 設定頁的「要重畫」旗標，與畫那一格用的頁面文字
REDRAW_FLAG = {"background_generation_id": "redraw_background", "pose_generation_id": "redraw_pose"}
SLOT_TEXT = {"background_generation_id": "scene_prompt", "pose_generation_id": "action_prompt"}


def slot_change_values(field: str, gen_id: int, *, new_selection: bool = True,
                       landed_text: str | None = None) -> dict:
    """條件式 UPDATE 用的 SET 片段：把頁面的 field（background_generation_id／pose_generation_id）設成 gen_id。
    換了不同的圖（以寫入當下的舊值判斷）而且選定圖是合成圖：清掉選定圖並退回草稿；
    舊流程的 page 圖不動，直到新的合成圖取代它。
    new_selection（選圖、上傳、take、自動選圖）：同時清掉這一格的「要重畫」（使用者已經有新的圖）；
    復原／重做選回舊的圖不算新的選擇，傳 False、旗標留著。
    landed_text（worker 換上去的生成，gen.user_text）：只有寫入當下頁面的場景／動作還是這段文字才清旗標；
    排隊後設定頁又改了文字（較新的「要重畫」）就留著。使用者自己選的（選圖、上傳）不帶，一律清掉。"""
    selected_is_composite = (
        select(Generation.id)
        .where(col(Generation.id) == col(Page.selected_generation_id),
               col(Generation.kind) == GenKind.COMPOSITE)
        .correlate(Page)
        .exists()
    )
    clear = and_(getattr(Page, field).is_distinct_from(gen_id), selected_is_composite)
    values = {
        field: gen_id,
        "updated_at": utcnow(),
        "selected_generation_id": case((clear, null()), else_=col(Page.selected_generation_id)),
        **reset_review_when(clear),
    }
    if new_selection:
        flag = REDRAW_FLAG[field]
        if landed_text is None:
            values[flag] = False
        else:
            same = col(getattr(Page, SLOT_TEXT[field])) == landed_text
            values[flag] = case((same, False), else_=col(getattr(Page, flag)))
    return values


def usable_conditions(kind: GenKind) -> list:
    """背景／角色動作可以選用、合成的條件（SQL）；角色動作還要有遮罩。"""
    conds = [
        col(Generation.kind) == kind,
        col(Generation.status) == GenStatus.SUCCEEDED,
        col(Generation.discarded).is_(False),
        col(Generation.image_path).is_not(None),
    ]
    if kind == GenKind.POSE:
        conds.append(col(Generation.mask_path).is_not(None))
    return conds


def is_usable(gen: Generation | None, kind: GenKind, page_id: int) -> bool:
    return (
        gen is not None
        and gen.page_id == page_id
        and gen.kind == kind
        and gen.status == GenStatus.SUCCEEDED
        and not gen.discarded
        and bool(gen.image_path)
        and (kind != GenKind.POSE or bool(gen.mask_path))
    )


def store_outside_tx(session: Session, storage: Storage, gen: Generation, data: bytes) -> tuple[int, str]:
    """把後端自己產生的圖（上傳背景、合成圖）寫進 storage，寫入期間不持有 SQLite 寫入鎖。

    R2 的 put 是網路請求（可能很慢）；在寫入交易裡做會讓其他寫入者等到 database is locked。所以：
    先以 RUNNING、沒有圖提交這一列（拿到 id 才有 key；storage_gc 把 RUNNING 當成寫入中，不刪它的物件），
    交易外寫物件，再由呼叫端以 finish_stored／條件式 UPDATE 標成 SUCCEEDED 並記上 key。
    寫入失敗就刪掉暫存列再丟出原本的例外：列永遠不會指到不存在的物件（留下的物件由 storage_gc 清）。
    回傳 (id, key)。"""
    # 呼叫端在這之前只能有讀取（沒有未提交的寫入），這裡的 commit 只提交這一列
    gen.status = GenStatus.RUNNING
    gen.image_path = None
    session.add(gen)
    session.commit()
    rel = storage.generation_image(gen.book_id, gen.id)
    try:
        storage.put(rel, data)
    except Exception:
        abandon(session, storage, gen.id, rel)
        raise
    return gen.id, rel


def abandon(session: Session, storage: Storage, gen_id: int, rel: str) -> None:
    """刪掉還沒有圖的暫存列（只刪仍是 RUNNING、沒有 image_path 的），再盡力刪物件；失敗只記 log。"""
    session.rollback()
    try:
        session.exec(
            delete(Generation).where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.RUNNING,
                                     col(Generation.image_path).is_(None))
        )
        session.commit()
    except Exception:  # noqa: BLE001  刪不掉：列沒有圖，重啟時 requeue_interrupted 會把它標成失敗
        session.rollback()
        log.warning("刪除暫存的生成紀錄 %s 失敗", gen_id, exc_info=True)
    try:
        storage.delete(rel)
    except Exception:  # noqa: BLE001  留下的物件由 storage_gc 清
        log.warning("刪除物件 %s 失敗", rel, exc_info=True)


def finish_stored(session: Session, gen_id: int, rel: str, **values):
    """條件式 UPDATE：暫存列（RUNNING、沒有圖）→ SUCCEEDED 並記上 key；不 commit，回傳結果（看 rowcount）。"""
    return session.exec(
        update(Generation)
        .where(col(Generation.id) == gen_id, col(Generation.status) == GenStatus.RUNNING,
               col(Generation.image_path).is_(None))
        .values(status=GenStatus.SUCCEEDED, image_path=rel, finished_at=utcnow(), **values)
    )


def get_pil_image(storage: Storage, rel: str | None) -> Image.Image:
    """從 Storage 讀出圖片；key 是空的或物件不存在丟 FileNotFoundError。"""
    if not rel:
        raise FileNotFoundError(rel)
    try:
        data = storage.get(rel)
    except StorageNotFound:
        raise FileNotFoundError(rel) from None
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.copy()


def composite_params(bg_id: int, pose_id: int, placement: Placement, frame_found: bool | None = None) -> dict:
    """合成圖的 params。frame_found 只有肖像（placement.frame）才記：背景上有沒有找到框。"""
    params = {
        "background_generation_id": bg_id,
        "pose_generation_id": pose_id,
        "placement": placement.to_dict(),
    }
    if frame_found is not None:
        params["frame_found"] = frame_found
    return params


def portrait_placement(placement: Placement, interior: np.ndarray | None,
                       cutout_size: tuple[int, int]) -> Placement:
    """肖像（placement.frame）這次實際合成用的位置。
    找不到框：照存的位置一般合成，height 超過 1.0 夾到 1.0（只限這張合成圖；頁面存的位置不動）。
    找到框而且 autofit：cx、cy、height 換成 fit_portrait 算出的肖像位置（四捨五入到 4 位、夾在合法範圍；
    cutout_size 是角色去背外接框的尺寸）。其餘照存的位置。"""
    if interior is None:
        return replace(placement, height=min(placement.height, 1.0))
    if not placement.autofit:
        return placement
    cx, cy, height = fit_portrait(interior, cutout_size)
    return replace(placement, cx=min(1.0, max(0.0, round(cx, 4))), cy=min(1.0, max(0.0, round(cy, 4))),
                   height=min(2.0, max(0.1, round(height, 4))))


def find_composite(
    session: Session, storage: Storage, page_id: int, bg_id: int, pose_id: int, placement: Placement
) -> Generation | None:
    """同背景＋角色動作＋位置（Placement.key()）、同合成版本且物件還在的既有合成圖。
    只對符合條件的候選呼叫 storage.exists()（合成是 POST，不在每次顯示時走）。"""
    rows = session.exec(
        select(Generation)
        .where(col(Generation.page_id) == page_id, col(Generation.kind) == GenKind.COMPOSITE,
               col(Generation.status) == GenStatus.SUCCEEDED, col(Generation.discarded).is_(False))
        .order_by(col(Generation.id).desc())
    ).all()
    for g in rows:
        if composite_matches(g, bg_id, pose_id, placement) and g.image_path:
            try:
                if storage.exists(g.image_path):
                    return g
            except ValueError:
                continue
    return None


def _load_images(storage: Storage, bg: Generation, pose: Generation) -> tuple[Image.Image, Image.Image, Image.Image]:
    """背景、角色圖與遮罩；檔案不在、壞掉或路徑不合法丟 ComposeError(FILE_MISSING)。"""
    try:
        return (get_pil_image(storage, bg.image_path), get_pil_image(storage, pose.image_path),
                get_pil_image(storage, pose.mask_path))
    except (OSError, ValueError) as e:
        raise ComposeError(FILE_MISSING) from e


def _compose_error(e: ValueError) -> ComposeError:
    """合成丟的 ValueError（遮罩是空的、與角色圖尺寸不同、太細長）→ 給使用者看的 ComposeError。"""
    if str(e) == DEGENERATE_MASK:  # 已經說了「請重新產生角色動作」
        return ComposeError(DEGENERATE_MASK)
    return ComposeError(f"無法合成：{e}，請重新產生角色動作")


def compose_page(session: Session, storage: Storage, page_id: int) -> int:
    """用頁面目前的背景、角色動作與位置（沒存過就用預設）產生合成圖並設為選定圖片，回傳它的 id。
    完全相同的既有合成圖直接選回（修線一起回來）；選定圖換了就退回草稿、清角色檢查。
    呼叫端負責確認權限，而且呼叫前不可有未提交的寫入。無法合成丟 ComposeError（中文訊息）。"""
    page = session.get(Page, page_id)
    if page is None:
        raise ComposeError("找不到這一頁")
    session.refresh(page)
    bg = (session.get(Generation, page.background_generation_id)
          if page.background_generation_id is not None else None)
    if not is_usable(bg, GenKind.BACKGROUND, page_id):
        raise ComposeError(NEED_BACKGROUND)
    pose = (session.get(Generation, page.pose_generation_id)
            if page.pose_generation_id is not None else None)
    if not is_usable(pose, GenKind.POSE, page_id):
        raise ComposeError(NEED_POSE)
    stored = page_placement(page.placement_json)
    placement_json = page.placement_json

    images: tuple[Image.Image, Image.Image, Image.Image] | None = None  # 背景、角色圖、遮罩（讀過就留著）
    interior: np.ndarray | None = None
    frame_found: bool | None = None
    write_back: str | None = None  # autofit 算出的肖像位置（與選用新合成圖在同一個交易寫回頁面）
    placement = stored
    if stored.frame is not None:
        # 肖像：先找框、決定這次的位置，才比對既有合成圖（位置一樣就直接選回）
        images = _load_images(storage, bg, pose)
        interior = find_interior(images[0], stored.frame)
        frame_found = interior is not None
        try:
            size = cutout(images[1], images[2]).size
        except ValueError as e:
            raise _compose_error(e) from e
        placement = portrait_placement(stored, interior, size)
        if interior is not None and stored.autofit and json.dumps(placement.to_dict()) != placement_json:
            write_back = json.dumps(placement.to_dict())

    target = find_composite(session, storage, page_id, bg.id, pose.id, placement)
    written: str | None = None  # 這次新寫的合成圖（列還是暫存的 RUNNING）
    if target is None:
        started_at, t0 = utcnow(), time.monotonic()
        bg_img, pose_img, mask_img = images or _load_images(storage, bg, pose)
        try:
            out = compose(bg_img, pose_img, mask_img, placement, interior)
        except ValueError as e:
            raise _compose_error(e) from e
        buf = io.BytesIO()
        out.save(buf, "PNG")
        params = json.dumps(composite_params(bg.id, pose.id, placement, frame_found), ensure_ascii=False,
                            sort_keys=True)
        target = Generation(
            book_id=page.book_id,
            page_id=page.id,
            kind=GenKind.COMPOSITE,
            user_text="",
            prompt="",
            negative_prompt="",
            seed=0,
            width=out.width,
            height=out.height,
            request_json=params,
            engine="compose",
            workflow_name="compose",
            workflow_version=COMPOSE_VERSION,
            models_json="[]",
            params_json=params,
            started_at=started_at,
        )
        _, written = store_outside_tx(session, storage, target, buf.getvalue())
        elapsed_s = time.monotonic() - t0
    target_id = target.id
    try:
        # 新合成圖標成成功與選用它在同一個交易：選不到（輸入被改）就整個回滾、刪掉暫存列與物件
        if written is not None and finish_stored(session, target_id, written, elapsed_s=elapsed_s).rowcount == 0:
            raise ComposeError(INPUTS_CHANGED)
        target_valid = (
            select(Generation.id)
            .where(col(Generation.id) == target_id, col(Generation.page_id) == page_id,
                   col(Generation.kind) == GenKind.COMPOSITE,
                   col(Generation.status) == GenStatus.SUCCEEDED,
                   col(Generation.discarded).is_(False),
                   col(Generation.image_path).is_not(None))  # storage_gc 清掉的舊合成圖不能選回
            .exists()
        )
        changed = col(Page.selected_generation_id).is_distinct_from(target_id)
        # 讀取後若背景／角色動作／位置被改（例如另一個分頁），不要用舊的輸入蓋掉
        res = session.exec(
            update(Page)
            .where(
                col(Page.id) == page_id,
                col(Page.background_generation_id) == bg.id,
                col(Page.pose_generation_id) == pose.id,
                col(Page.placement_json).is_not_distinct_from(placement_json),
                target_valid,
            )
            .values(selected_generation_id=target_id, updated_at=utcnow(),
                    **({"outline_fill": True} if written is not None else {}),  # 新合成圖用單線輪廓；重用舊的不動
                    # autofit 的肖像位置寫回頁面：只是位置欄位（使用者的意圖沒變），不存復原快照、不動筆畫
                    **({"placement_json": write_back} if write_back is not None else {}),
                    **reset_review_when(changed))
        )
        if res.rowcount == 0:
            raise ComposeError(INPUTS_CHANGED)
        session.commit()
    except Exception:
        session.rollback()
        if written is not None:
            abandon(session, storage, target_id, written)
        raise
    return target_id
