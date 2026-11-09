"""引用 scan 共用的解析与路径规则；不执行 scan，也不读取数据库或媒体。

先提取四类引用，再按本项目约定替换 /_files/、跳过配置中的外链前缀、去掉
查询参数/锚点，给无后缀普通引用补 .md。仅 / 和 ./ 可用，block 禁止 ./。
归一化得到绝对路径后拒绝自引用；目标存在性由对应 scan 单独决定。

特别约定：/_files/ 只是前缀替换，不保留 Wiki.js 原生的资源命名空间。
[下载](/_files/docs/manual) 必须得到 /docs/manual.md；图片和 gallery 不补后缀。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re
from urllib.parse import unquote

from .markdown import Reference, make_parser, parse_markdown
from .models import ScanContext


@dataclass(frozen=True)
class Resolution:
    target: str | None = None
    error: str | None = None
    skipped: bool = False


@dataclass(frozen=True)
class ResolvedReference:
    source: str
    reference: Reference
    target: str


@dataclass(frozen=True)
class ReferenceAnalysis:
    references: tuple[ResolvedReference, ...]
    errors: tuple[str, ...]


def validate_empty_options(options: dict) -> None:
    if not isinstance(options, dict) or options:
        raise ValueError("本 scan 不接受独立 options；引用前缀配置位于 global.external_prefixes")


def resolve_reference(reference: Reference, source: str,
                      external_prefixes: tuple[str, ...] = ("http", "mailto")) -> Resolution:
    """规范化地址，返回格式诊断；本函数完全不查询目标是否存在。"""
    path = reference.href
    while path.startswith("/_files/"):
        path = "/" + path[len("/_files/"):]
    if path.startswith(external_prefixes):
        return Resolution(skipped=True)
    path = re.split(r"[?#]", path, maxsplit=1)[0]
    if not path:
        return Resolution(error="禁止自引用（空地址或仅包含查询参数、锚点）")
    # MarkdownIt 会把中文和空格等 URL 编码；分隔参数之后再解码，保留文件名中
    # 通过 %23 / %3F 表示的字面字符。+ 是普通路径字符，不按表单规则转为空格。
    path = unquote(path)
    if reference.kind in {"link", "grid"} and not PurePosixPath(path).suffix:
        path += ".md"
    if path.startswith("/"):
        target = posixpath.normpath("/" + path.lstrip("/"))
    elif path.startswith("./"):
        if reference.in_block or reference.kind in {"grid", "gallery"}:
            return Resolution(error="block 中禁止使用相对路径")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    else:
        return Resolution(error="内部引用必须以 / 或 ./ 开头")
    if target == source:
        return Resolution(error="禁止自引用", target=target)
    return Resolution(target=target)


def describe_reference(item: ResolvedReference) -> str:
    return f"{item.source}:{item.reference.line}  {item.reference.href!r} → {item.target}"


def analyze_references(context: ScanContext) -> ReferenceAnalysis:
    """分析当前 scan 可见的所有页面；不同 scan 的过滤和结果互不影响。"""
    found, errors = [], []
    parser = make_parser()
    for page in context.snapshot.pages:
        parsed = parse_markdown(page.content, parser)
        errors.extend(f"{page.path}:{issue.line}  {issue.message}" for issue in parsed.issues)
        for reference in parsed.references:
            resolution = resolve_reference(reference, page.path, context.settings.external_prefixes)
            if resolution.error:
                errors.append(f"{page.path}:{reference.line}  {reference.href!r}：{resolution.error}")
            elif not resolution.skipped:
                assert resolution.target is not None
                found.append(ResolvedReference(page.path, reference, resolution.target))
    return ReferenceAnalysis(tuple(found), tuple(errors))
