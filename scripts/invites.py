#!/usr/bin/env python3
"""管理邀請連結（一個連結＝一個 owner）。DB 存 token 的 sha256（查詢用）與加密版（管理頁／--show-links 再顯示連結用，金鑰見 auth.link_key）。

用法（在專案根目錄執行）：
  python scripts/invites.py [--settings PATH] create --name 名字 [--admin] [--cap 60] [--base-url URL]
  python scripts/invites.py list [--show-links] [--base-url URL]   --show-links 多一欄「連結」（解密後顯示；舊資料顯示 -）
  python scripts/invites.py rotate --id N [--base-url URL]    換發新連結（舊連結與已登入的瀏覽器都失效）
  python scripts/invites.py revoke --id N                     撤銷（連結失效，已登入的瀏覽器下一個請求就 401）
  python scripts/invites.py delete-data --id N [--yes] [--force-admin]
      刪除這個 owner 的所有書、頁面、生成紀錄、修線、物件（books/{book_id}/ 與預覽）、session、用量，最後刪 owner。
      沒有 --yes 時要輸入 owner 名稱確認。最後一個（未撤銷的）admin 要加 --force-admin 才刪。
      DB 的 storage_instance_id 與 storage 的 books/_instance.json 必須一致，否則什麼都不刪（結束碼 4；
      讀不到標記結束碼 5）；補標記見 scripts/storage_gc.py --init-instance。

設定與 app 相同（config/settings.yaml 或 APP_SETTINGS_FILE，APP_ 環境變數覆寫；R2 憑證只來自環境變數）。
連結前綴：--base-url，其次 auth.invite_base_url，都沒有就是 http://localhost:3000。
結束碼：0 成功；1 操作失敗或取消；2 設定錯誤；4 DB 與 storage 不是一組；5 讀不到 storage 的身分標記。
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, col, delete, select, update  # noqa: E402

from app.config import Settings, SettingsError, load_settings  # noqa: E402
from app.db import init_db, make_engine, session_factory  # noqa: E402
from app.models import (  # noqa: E402
    Book,
    Generation,
    GenStatus,
    LineartEdit,
    Owner,
    OwnerSession,
    Page,
    Usage,
    utcnow,
)
from app.services import auth, instance  # noqa: E402
from app.services.usage import clock, refund_generations, taipei_day  # noqa: E402
from app.storage import make_storage  # noqa: E402

DEFAULT_BASE_URL = "http://localhost:3000"


class CliError(Exception):
    """要回報給使用者的錯誤（結束碼 1）。"""


def open_db(settings: Settings):
    """與 app 相同：建 engine、升級到最新 schema、回傳 session factory。"""
    db_path = settings.paths.database
    if not db_path.parent.is_dir():
        raise SettingsError([f"paths.database 的父資料夾不存在：{db_path.parent}"])
    engine = make_engine(db_path)
    init_db(engine)
    return session_factory(engine)


def base_url(args: argparse.Namespace, settings: Settings) -> str:
    return (args.base_url or settings.auth.invite_base_url or DEFAULT_BASE_URL).strip().rstrip("/")


def print_link(url: str, token: str, owner: Owner) -> None:
    print(f"owner {owner.id}「{owner.name}」的邀請連結（只顯示這一次，請直接傳給對方）：")
    print(f"{url}/i/{token}")


def get_owner(s: Session, owner_id: int) -> Owner:
    owner = s.get(Owner, owner_id)
    if owner is None:
        raise CliError(f"找不到 owner {owner_id}")
    return owner


# --- create / rotate / revoke ---


def cmd_create(args: argparse.Namespace, settings: Settings, sessions) -> int:
    name = (args.name or "").strip()
    if not name:
        raise CliError("--name 不可為空白")
    if args.cap < 0:
        raise CliError("--cap 不可小於 0")
    url = base_url(args, settings)
    with sessions() as s:
        owner, token = auth.create_owner(
            s, name, is_admin=args.admin, cap=args.cap, link_key=auth.link_key(settings)
        )
    print_link(url, token, owner)
    return 0


def cmd_rotate(args: argparse.Namespace, settings: Settings, sessions) -> int:
    url = base_url(args, settings)
    with sessions() as s:
        owner = get_owner(s, args.id)
        if owner.revoked_at is not None:
            raise CliError(f"owner {owner.id}「{owner.name}」已撤銷，不發新連結")
        token = auth.rotate_token(s, owner, auth.link_key(settings))
        s.commit()
    print_link(url, token, owner)
    print("舊連結與已登入的瀏覽器都已失效。")
    return 0


def cmd_revoke(args: argparse.Namespace, settings: Settings, sessions) -> int:
    with sessions() as s:
        owner = get_owner(s, args.id)
        if owner.revoked_at is None:
            owner.revoked_at = utcnow()
            s.add(owner)
        n = auth.delete_sessions(s, owner.id)
        s.commit()
    print(f"已撤銷 owner {owner.id}「{owner.name}」，刪除 {n} 個 session。")
    return 0


# --- list ---


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _table(rows: list[list[str]]) -> str:
    widths = [max(_width(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell + " " * (widths[i] - _width(cell)) for i, cell in enumerate(r)).rstrip() for r in rows
    )


def cmd_list(args: argparse.Namespace, settings: Settings, sessions) -> int:
    today = taipei_day(clock())
    prefix = base_url(args, settings) if args.show_links else ""
    key = auth.link_key(settings) if args.show_links else b""
    with sessions() as s:
        owners = s.exec(select(Owner).order_by(col(Owner.id))).all()
        used = dict(s.exec(select(Usage.owner_id, Usage.generations).where(col(Usage.day) == today)).all())
        n_sessions = dict(
            s.exec(select(OwnerSession.owner_id, func.count()).group_by(col(OwnerSession.owner_id))).all()
        )
    # 名稱裡的空白換成底線，一列永遠是 7 欄（方便 grep／awk）；--show-links 才多第 8 欄「連結」
    rows = [["id", "name", "admin", "cap", "today", "revoked", "sessions"] + (["連結"] if args.show_links else [])]
    for o in owners:
        rows.append([
            str(o.id),
            "_".join(o.name.split()) or "-",
            "yes" if o.is_admin else "no",
            "unlimited" if o.is_admin else str(o.daily_generation_cap),
            str(used.get(o.id, 0)),
            "yes" if o.revoked_at is not None else "no",
            str(n_sessions.get(o.id, 0)),
        ] + ([auth.reveal_link(o, key, prefix) or "-"] if args.show_links else []))
    print(_table(rows))
    print(f"（today＝台北 {today.isoformat()} 已排入的生成數）")
    return 0


# --- delete-data ---


def _active_admins_except(s: Session, owner_id: int) -> int:
    return s.exec(
        select(func.count()).select_from(Owner).where(
            col(Owner.is_admin), col(Owner.revoked_at).is_(None), col(Owner.id) != owner_id
        )
    ).one()


def _check_last_admin(s: Session, owner: Owner, force: bool) -> None:
    if owner.is_admin and _active_admins_except(s, owner.id) == 0 and not force:
        raise CliError(f"owner {owner.id}「{owner.name}」是最後一個 admin；確定要刪就加 --force-admin")


def cmd_delete_data(args: argparse.Namespace, settings: Settings, sessions) -> int:
    storage = make_storage(settings)
    with sessions() as s:
        # DB 與 storage 不是一組時，books/{id}/ 是別人的活物件：在詢問確認與任何 DB 變更之前就拒絕
        instance.require_instance(s, storage, tool="invites.py delete-data")
        owner = get_owner(s, args.id)
        _check_last_admin(s, owner, args.force_admin)
        book_ids = list(s.exec(select(Book.id).where(col(Book.owner_id) == owner.id)).all())
        n_pages = s.exec(select(func.count()).select_from(Page).where(col(Page.book_id).in_(book_ids))).one()
        n_gens = s.exec(
            select(func.count()).select_from(Generation).where(col(Generation.book_id).in_(book_ids))
        ).one()
        name = owner.name
    print(f"owner {args.id}「{name}」：{len(book_ids)} 本書、{n_pages} 頁、{n_gens} 筆生成紀錄。")
    if not args.yes:
        try:
            answer = input(f"這會永久刪除以上資料與圖片。輸入名稱「{name}」確認：")
        except EOFError:  # 沒有互動式輸入（例如排程）：視同取消
            answer = ""
        if answer.strip() != name:
            print("名稱不符，已取消。", file=sys.stderr)
            return 1

    with sessions() as s:
        # 先取消排隊中的（條件式 UPDATE 取得寫入鎖，worker 之後無法再認領），再檢查有沒有執行中的。
        # 書單用子查詢：確認期間 owner 新建的書也一起刪
        owned = select(Book.id).where(col(Book.owner_id) == args.id)
        cancelled = s.exec(
            update(Generation)
            .where(col(Generation.book_id).in_(owned), col(Generation.status) == GenStatus.QUEUED)
            .values(status=GenStatus.CANCELLED, error="資料已刪除", finished_at=utcnow())
            .returning(Generation.id)
        ).scalars().all()
        refund_generations(s, cancelled)  # 替別人的書排隊的 admin 也拿回額度
        # 確認期間可能有變（另一個 admin 被撤銷、owner 已被刪）：在持有寫入鎖的交易裡再檢查一次
        try:
            _check_last_admin(s, get_owner(s, args.id), args.force_admin)
        except CliError:
            s.rollback()
            raise
        running = s.exec(
            select(func.count()).select_from(Generation).where(
                col(Generation.book_id).in_(owned), col(Generation.status) == GenStatus.RUNNING
            )
        ).one()
        if running:
            s.rollback()
            raise CliError("這個 owner 還有圖片正在生成，請稍後再刪")
        book_ids = list(s.exec(owned).all())
        gen_ids = set(s.exec(select(Generation.id).where(col(Generation.book_id).in_(book_ids))).all())
        n_pages = s.exec(select(func.count()).select_from(Page).where(col(Page.book_id).in_(book_ids))).one()
        page_ids = select(Page.id).where(col(Page.book_id).in_(book_ids))
        s.exec(delete(LineartEdit).where(
            col(LineartEdit.page_id).in_(page_ids) | col(LineartEdit.generation_id).in_(list(gen_ids))
        ))
        s.exec(delete(Generation).where(col(Generation.book_id).in_(book_ids)))
        s.exec(delete(Page).where(col(Page.book_id).in_(book_ids)))
        s.exec(delete(Book).where(col(Book.id).in_(book_ids)))
        n_sessions = auth.delete_sessions(s, args.id)
        s.exec(delete(Usage).where(col(Usage.owner_id) == args.id))
        s.exec(delete(Owner).where(col(Owner.id) == args.id))
        s.commit()

    # 物件在交易提交後才刪（盡力而為）：失敗只記下來，留下的孤兒物件由 storage_gc 清
    failed = 0
    for book_id in book_ids:
        prefix = f"books/{book_id}/"  # 結尾的 / 不可省：books/1 會連 books/10 一起刪
        try:
            storage.delete_prefix(prefix)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"刪除 {prefix} 失敗：{type(e).__name__}: {e}", file=sys.stderr)
    previews = 0
    try:
        for key in list(storage.list("cache/previews/")):
            gid = _preview_gen_id(key)
            if gid is not None and gid in gen_ids:
                try:
                    storage.delete(key)
                    previews += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    print(f"刪除 {key} 失敗：{type(e).__name__}: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        failed += 1
        print(f"列出預覽失敗：{type(e).__name__}: {e}", file=sys.stderr)
    print(
        f"已刪除 owner {args.id}「{name}」：{len(book_ids)} 本書、{n_pages} 頁、{len(gen_ids)} 筆生成紀錄"
        f"（取消排隊 {len(cancelled)} 筆）、{n_sessions} 個 session、預覽 {previews} 個。"
    )
    if failed:
        print(f"有 {failed} 個物件刪除失敗；之後執行 scripts/storage_gc.py 會清掉。", file=sys.stderr)
        return 1
    return 0


def _preview_gen_id(key: str) -> int | None:
    """cache/previews/{gen}_{key}.png → gen；格式不符回 None。"""
    name = key.rsplit("/", 1)[-1]
    head, sep, _ = name.partition("_")
    return int(head) if sep and head.isdigit() else None


COMMANDS = {
    "create": cmd_create,
    "list": cmd_list,
    "rotate": cmd_rotate,
    "revoke": cmd_revoke,
    "delete-data": cmd_delete_data,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--settings", type=Path, help="設定檔（預設 config/settings.yaml 或 APP_SETTINGS_FILE）")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="建立邀請，印出連結（只印一次）")
    c.add_argument("--name", required=True)
    c.add_argument("--admin", action="store_true", help="admin：看得到所有人的書，不設每日上限")
    c.add_argument("--cap", type=int, default=60, help="每日生成上限（預設 60；admin 不受限）")
    c.add_argument("--base-url", help=f"連結前綴（預設 auth.invite_base_url 或 {DEFAULT_BASE_URL}）")

    ls = sub.add_parser("list", help="列出邀請（預設不顯示連結）")
    ls.add_argument("--show-links", action="store_true", help="多一欄「連結」（需要金鑰；舊資料顯示 -）")
    ls.add_argument("--base-url", help=f"連結前綴（預設 auth.invite_base_url 或 {DEFAULT_BASE_URL}）")

    r = sub.add_parser("rotate", help="換發新連結；舊連結與已登入的瀏覽器失效")
    r.add_argument("--id", type=int, required=True)
    r.add_argument("--base-url")

    v = sub.add_parser("revoke", help="撤銷邀請並刪除它的 session")
    v.add_argument("--id", type=int, required=True)

    d = sub.add_parser("delete-data", help="刪除 owner 與它的所有資料、圖片")
    d.add_argument("--id", type=int, required=True)
    d.add_argument("--yes", action="store_true", help="不詢問確認")
    d.add_argument("--force-admin", action="store_true", help="允許刪除最後一個 admin")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.settings, check_paths=False)
        sessions = open_db(settings)
        return COMMANDS[args.command](args, settings, sessions)
    except SettingsError as e:
        print(e, file=sys.stderr)
        return 2
    except CliError as e:
        print(e, file=sys.stderr)
        return 1
    except instance.InstanceMismatch as e:
        print(e, file=sys.stderr)
        return 4
    except instance.InstanceUnavailable as e:
        print(f"{e}\n無法確認資料庫與 storage 是同一組，什麼都沒刪。請檢查 storage 設定、憑證與網路後再試。", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
