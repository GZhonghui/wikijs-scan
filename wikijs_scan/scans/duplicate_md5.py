"""按数据库中资源二进制内容的 MD5 查找疑似重复资源。

流程：计算每个资源的 MD5，相同摘要对应两个或更多资源即失败，日志列出
摘要及组内全部路径。大小元数据不参与判重，页面不参与比较。
缺失或无效的二进制内容明确失败，空字节串则是有效的空文件内容。
"""

from collections import defaultdict
import hashlib

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


def validate_options(options: dict) -> None:
    validate_keys(options, set())


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    groups: dict[str, list[str]] = defaultdict(list)
    sections = []
    for asset in sorted(context.snapshot.assets, key=lambda item: item.path):
        if not isinstance(asset.content, bytes):
            sections.append(f"{asset.path}：资源二进制内容缺失或无效，无法计算 MD5")
        else:
            digest = hashlib.md5(asset.content, usedforsecurity=False).hexdigest()
            groups[digest].append(asset.path)
    for digest, paths in sorted(groups.items()):
        if len(paths) > 1:
            sections.append(f"MD5 {digest}（{len(paths)} 个资源）：\n" + "\n".join(paths))
    return ScanResult(not sections, "\n\n".join(sections))
