"""引用格式扫描：解析四种引用，检查前缀、block 相对路径及自引用。

流程：用公共解析器读取全部可见页面，收集解析错误和路径格式诊断。
任一格式错误或无法解析的 block 使本项失败；不检查引用目标是否存在。
本项按方案禁用白名单，并依赖 inventory；依赖和过滤由 main 统一执行。
只返回 ScanResult，不打印、写文件或调用其他 scan。
"""

from ..models import ScanContext, ScanResult
from ..references import analyze_references, validate_empty_options


def validate_options(options: dict) -> None:
    validate_empty_options(options)


def run(context: ScanContext, options: dict) -> ScanResult:
    errors = analyze_references(context).errors
    return ScanResult(not errors, "\n".join(errors))
