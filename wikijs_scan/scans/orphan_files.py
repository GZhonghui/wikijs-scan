"""零入链扫描：寻找当前过滤视图中未被任何页面引用的页面和资源。

流程：先为所有文件建立零计数，再统计规范引用指向的现存文件；不存在的
目标不生成文件记录。任意零入链文件使本项失败，日志列出完整路径。
自引用属于前置格式错误；本项自身遇到解析/格式错误也失败，防止漏扫。
这是入链统计，不是首页可达性或弱连通扫描；各项之间不调用、不共享状态。
"""

from ..models import ScanContext, ScanResult
from ..references import analyze_references, validate_empty_options


def validate_options(options: dict) -> None:
    validate_empty_options(options)


def run(context: ScanContext, options: dict) -> ScanResult:
    analysis = analyze_references(context)
    incoming = dict.fromkeys(context.snapshot.file_paths, 0)
    for item in analysis.references:
        if item.target in incoming:
            incoming[item.target] += 1
    orphans = sorted(path for path, count in incoming.items() if count == 0)
    sections = list(analysis.errors)
    if orphans:
        sections.append(f"游离文件（{len(orphans)} 个）：\n" + "\n".join(orphans))
    return ScanResult(not sections, "\n".join(sections))
