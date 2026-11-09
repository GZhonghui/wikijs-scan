"""在基础数据层为单个 scan 建立独立视图，不修改数据库或原始快照。

忽略规则是区分大小写的纯字符串前缀匹配，不是 glob：/home 同时匹配
/home.md、/home/ 和 /homepage.md；/home/ 不匹配 /home.md。
被过滤的页面连同其出链消失，被过滤的目标也不再能被当前 scan 找到。
"""

from .models import Snapshot


def filter_snapshot(snapshot: Snapshot, prefixes: tuple[str, ...]) -> Snapshot:
    if not prefixes:
        return snapshot
    return Snapshot(
        pages=tuple(page for page in snapshot.pages if not page.path.startswith(prefixes)),
        assets=tuple(asset for asset in snapshot.assets if not asset.path.startswith(prefixes)),
        folders=tuple(folder for folder in snapshot.folders if not folder.path.startswith(prefixes)),
    )
