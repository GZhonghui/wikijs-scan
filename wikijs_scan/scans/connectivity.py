"""弱连通扫描：判断当前可见的页面和资源是否属于一个连通分量。

流程：所有文件均作为节点，规范且指向现存文件的引用作为有向边；计算弱
连通性时忽略边的方向。用并查集合并节点，因此允许引用环和共享资源。
只有一个连通分量且没有解析/格式错误时成功；空图也失败。失败日志记录
分量数量和每组一个确定性的代表路径，不罗列整个图。此项不判断零入链。
"""

from ..models import ScanContext, ScanResult
from ..references import analyze_references, validate_empty_options


def validate_options(options: dict) -> None:
    validate_empty_options(options)


def run(context: ScanContext, options: dict) -> ScanResult:
    analysis = analyze_references(context)
    parents = {path: path for path in context.snapshot.file_paths}

    def find(path: str) -> str:
        while parents[path] != path:
            parents[path] = parents[parents[path]]
            path = parents[path]
        return path

    for item in analysis.references:
        if item.target in parents:
            source_root, target_root = find(item.source), find(item.target)
            if source_root != target_root:
                parents[source_root] = target_root
    representatives: dict[str, str] = {}
    for path in sorted(parents):
        representatives.setdefault(find(path), path)
    count = len(representatives)
    sections = list(analysis.errors)
    if count != 1:
        sections.append(f"弱连通分量数量：{count}")
        sections.extend(f"代表节点：{path}" for path in sorted(representatives.values()))
    return ScanResult(not sections, "\n".join(sections))
