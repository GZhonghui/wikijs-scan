"""按字节数查找疑似重复资源，不比较文件内容或计算散列。

流程：将资源按大小分组，同一字节数对应两个或更多资源即失败，日志列出
这个字节数及组内全部路径。相同大小只是疑似重复，仍按项目规则判失败。
大小元数据缺失或无效也失败；页面不参与比较，不读取任何媒体内容。
"""

from collections import defaultdict

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


def validate_options(options: dict) -> None:
    validate_keys(options, set())


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    groups: dict[int, list[str]] = defaultdict(list)
    sections = []
    for asset in sorted(context.snapshot.assets, key=lambda item: item.path):
        if type(asset.size) is not int or asset.size < 0:
            sections.append(f"{asset.path}：资源大小缺失或无效（{asset.size!r}）")
        else:
            groups[asset.size].append(asset.path)
    for size, paths in sorted(groups.items()):
        if len(paths) > 1:
            sections.append(f"{size} 字节（{len(paths)} 个资源）：\n" + "\n".join(paths))
    return ScanResult(not sections, "\n\n".join(sections))
