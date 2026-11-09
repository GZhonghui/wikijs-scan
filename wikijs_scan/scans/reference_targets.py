"""目标存在性扫描：检查当前过滤视图中的内部引用是否对应真实文件记录。

流程：独立提取并规范化引用，用页面及资源路径索引检查目标；目录不是文件。
有不存在的目标则失败。存在性不涉及媒体二进制或存储后端；解析/格式错误
同样失败，避免调用者绕过依赖时误报成功。只返回结果，不调用其他 scan。
"""

from ..models import ScanContext, ScanResult
from ..references import analyze_references, describe_reference, validate_empty_options


def validate_options(options: dict) -> None:
    validate_empty_options(options)


def run(context: ScanContext, options: dict) -> ScanResult:
    analysis = analyze_references(context)
    existing = context.snapshot.file_paths
    missing = [describe_reference(item) for item in analysis.references if item.target not in existing]
    sections = list(analysis.errors)
    if missing:
        sections.append(f"目标不存在（{len(missing)} 处）：\n" + "\n".join(missing))
    return ScanResult(not sections, "\n".join(sections))
