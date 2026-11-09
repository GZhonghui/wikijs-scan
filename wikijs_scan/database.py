"""只读加载 Wiki.js 3.0.2 的页面正文、资源元数据和显式目录。

流程：在一个 REPEATABLE READ、READ ONLY 事务中读取 sites/pages/tree/assets，
验证单站点、en 语言和 Markdown 页面前提，再将 tree.folderPath 的逐段 UTF-8
十六进制编码还原为路径。页面统一附加 .md，空目录保留；不读取媒体 data/preview，
不访问文件缓存或存储目标，不修改数据库，也不让各 scan 自行发起 SQL。

结构不完整时明确失败，不用内连接静默丢弃缺失 tree 行的页面或资源。
字段依据：github.com/requarks/wiki/blob/3.0.2/backend/db/schema.ts
目录编码：github.com/requarks/wiki/blob/3.0.2/backend/helpers/common.ts
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
import re
from typing import Any

from .models import Asset, Folder, Page, Snapshot


class DatabaseError(ValueError):
    """数据库连接、结构或方案前提不满足，无法得到完整扫描快照。"""


SITE_SQL = 'SELECT id FROM public.sites ORDER BY id'
PAGE_SQL = '''
    SELECT id, path, title, content, "contentType" AS content_type, locale
    FROM public.pages WHERE "siteId" = %s ORDER BY path, id
'''
TREE_SQL = '''
    SELECT id, "folderPath"::text AS folder_path, "fileName" AS file_name,
           "tree"::text AS kind, locale, title
    FROM public.tree WHERE "siteId" = %s ORDER BY "folderPath", "fileName", id
'''
ASSET_SQL = '''
    SELECT id, "fileName" AS file_name, "fileSize" AS file_size
    FROM public.assets WHERE "siteId" = %s ORDER BY id
'''


def _segment(value: Any, label: str) -> str:
    # 命名规范由 scan 检查。此处只拒绝无法无歧义地放进逻辑目录树的结构。
    if (not isinstance(value, str) or value in {"", ".", ".."}
            or any(char in value for char in ("/", "\\", "\x00"))):
        raise DatabaseError(f"{label} 不是有效的单级文件名：{value!r}")
    return value


def decode_tree_path(encoded: str | None) -> str:
    """返回无首尾斜杠的父目录路径；NULL 和空字符串均表示站点根。"""
    if encoded is None or encoded == "":
        return ""
    if not isinstance(encoded, str):
        raise DatabaseError("tree.folderPath 必须是文本或 NULL")
    decoded = []
    for label in encoded.split("."):
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", label):
            raise DatabaseError(f"tree.folderPath 含无效十六进制标签：{label!r}")
        try:
            segment = bytes.fromhex(label).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DatabaseError(f"tree.folderPath 标签不是 UTF-8：{label!r}") from exc
        decoded.append(_segment(segment, "tree.folderPath"))
    return "/".join(decoded)


def _page_path(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip("/"):
        raise DatabaseError(f"pages.path 必须是不含首尾斜杠的非空路径：{value!r}")
    for part in value.split("/"):
        _segment(part, "pages.path")
    return "/" + value


def _text(value: Any, label: str, *, nullable: bool = False) -> str:
    if nullable and value is None:
        return ""
    if not isinstance(value, str):
        raise DatabaseError(f"{label} 必须是文本")
    return value


def _indexed(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    result = {}
    for row in rows:
        key = str(row["id"])
        if key in result:
            raise DatabaseError(f"{label} 存在重复 id：{key}")
        result[key] = row
    return result


def snapshot_from_rows(
    page_rows: Sequence[Mapping[str, Any]],
    tree_rows: Sequence[Mapping[str, Any]],
    asset_rows: Sequence[Mapping[str, Any]],
) -> Snapshot:
    """把已读取的行转换为不可变快照；纯函数，便于验证真实 schema 的边界。"""
    page_by_id = _indexed(page_rows, "pages")
    asset_by_id = _indexed(asset_rows, "assets")
    tree_by_id = _indexed(tree_rows, "tree")
    pages, assets, folders = [], [], []
    logical_paths: set[str] = set()
    tree_paths: dict[str, str] = {}

    for row_id, row in tree_by_id.items():
        if row["locale"] != "en":
            raise DatabaseError(f"仅支持 en 语言，tree {row_id} 的语言为 {row['locale']!r}")
        parent = decode_tree_path(row["folder_path"])
        name = _segment(row["file_name"], f"tree {row_id}.fileName")
        raw_path = "/" + "/".join(part for part in (parent, name) if part)
        tree_paths[row_id] = raw_path
        kind = row["kind"]
        if kind == "folder":
            item = Folder(raw_path + "/", _text(row["title"], "tree.title"))
            folders.append(item)
        elif kind == "page":
            page = page_by_id.get(row_id)
            if page is None:
                raise DatabaseError(f"tree 页面缺少对应的 pages 记录：{raw_path}")
            if page["locale"] != "en":
                raise DatabaseError(f"仅支持 en 语言：{raw_path}")
            if page["content_type"] != "markdown":
                raise DatabaseError(f"页面不是 Markdown：{raw_path} ({page['content_type']!r})")
            if _page_path(page["path"]) != raw_path:
                raise DatabaseError(f"pages.path 与 tree 路径不一致：{page['path']!r} / {raw_path}")
            # 追加的是扫描器的逻辑扩展名，原页面名本身若有点号也不会被截断。
            item = Page(raw_path + ".md", _text(page["title"], "pages.title"),
                        _text(page["content"], "pages.content", nullable=True))
            pages.append(item)
        elif kind == "asset":
            asset = asset_by_id.get(row_id)
            if asset is None:
                raise DatabaseError(f"tree 资源缺少对应的 assets 记录：{raw_path}")
            if asset["file_name"] != name:
                raise DatabaseError(f"assets.fileName 与 tree 文件名不一致：{raw_path}")
            size = asset["file_size"]
            if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
                raise DatabaseError(f"资源字节数无效：{raw_path} ({size!r})")
            item = Asset(raw_path, size)
            assets.append(item)
        else:
            raise DatabaseError(f"未知 tree 类型：{kind!r}")
        if item.path in logical_paths:
            raise DatabaseError(f"逻辑路径重复，无法唯一确定目标：{item.path}")
        logical_paths.add(item.path)

    for label, records, kind in (("pages", page_by_id, "page"), ("assets", asset_by_id, "asset")):
        for row_id in records:
            if row_id not in tree_by_id or tree_by_id[row_id]["kind"] != kind:
                raise DatabaseError(f"{label} 记录缺少同类型 tree 记录：{row_id}")

    # 正常 Wiki.js 会为每一级目录建立显式记录，缺失时不猜测标题、不伪造目录。
    folder_paths = {folder.path.rstrip("/") for folder in folders}
    for raw_path in tree_paths.values():
        parent = str(PurePosixPath(raw_path).parent)
        if parent != "/" and parent not in folder_paths:
            raise DatabaseError(f"目录树缺少父目录记录：{parent}（来自 {raw_path}）")

    return Snapshot(
        pages=tuple(sorted(pages, key=lambda item: item.path)),
        assets=tuple(sorted(assets, key=lambda item: item.path)),
        folders=tuple(sorted(folders, key=lambda item: item.path)),
    )


def load_snapshot(database: Mapping[str, Any]) -> Snapshot:
    """只执行 SELECT；连接上下文负责在任何异常情况下关闭事务与连接。"""
    import psycopg
    from psycopg import IsolationLevel
    from psycopg.rows import dict_row

    connection_keys = ("host", "port", "dbname", "user", "password", "connect_timeout", "sslmode")
    kwargs = {key: database[key] for key in connection_keys if key in database}
    try:
        with psycopg.connect(**kwargs, row_factory=dict_row) as connection:
            connection.read_only = True
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)",
                               (str(database.get("statement_timeout_ms", 30000)),))
                cursor.execute(SITE_SQL)
                sites = cursor.fetchall()
                if len(sites) != 1:
                    raise DatabaseError(f"只支持一个站点，数据库中实际有 {len(sites)} 个")
                site_id = sites[0]["id"]
                cursor.execute(PAGE_SQL, (site_id,))
                pages = cursor.fetchall()
                cursor.execute(TREE_SQL, (site_id,))
                tree = cursor.fetchall()
                cursor.execute(ASSET_SQL, (site_id,))
                assets = cursor.fetchall()
                return snapshot_from_rows(pages, tree, assets)
    except psycopg.Error as exc:
        message = str(exc)
        password = database.get("password")
        if isinstance(password, str) and password:
            message = message.replace(password, "***")
        raise DatabaseError(f"无法读取 Wiki.js 数据库：{message}") from None
