"""数据库、过滤层和各 scan 之间的只读数据契约。

页面在内存中使用附加 .md 的逻辑路径，资源保留原文件名，目录路径以 / 结尾。
这些路径只描述 Wiki 内容树，不会用于创建本地文件。Page/Asset 保留数据来源，
避免把上传的同后缀资源误当作可以读取正文的页面。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    path: str
    title: str
    content: str


@dataclass(frozen=True)
class Asset:
    path: str
    size: int | None


@dataclass(frozen=True)
class Folder:
    path: str
    title: str


@dataclass(frozen=True)
class Snapshot:
    """一次一致性读取的内容；根目录 / 不作为 Folder 保存。"""

    pages: tuple[Page, ...] = ()
    assets: tuple[Asset, ...] = ()
    folders: tuple[Folder, ...] = ()

    @property
    def file_paths(self) -> frozenset[str]:
        return frozenset(item.path for item in (*self.pages, *self.assets))


@dataclass(frozen=True)
class GlobalSettings:
    images_dir: str = "images"
    docs_dir: str = "docs"
    external_prefixes: tuple[str, ...] = ("http", "mailto")


@dataclass(frozen=True)
class ScanContext:
    """只含本 scan 可见的数据；不同 scan 不传递中间结果。"""

    snapshot: Snapshot
    settings: GlobalSettings = GlobalSettings()


@dataclass(frozen=True)
class ScanResult:
    success: bool
    log_text: str = ""
