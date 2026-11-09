"""检查资源大小：只读数据库中的字节数，不读取媒体内容。

流程：遍历全部资源，验证大小元数据是非负整数，再与 max_bytes 比较。
默认阈值为 1 MiB（1,048,576 字节），等于阈值合法，大于阈值失败。
大小缺失或无效同样失败，不能将未知大小当作零；日志仅列异常资源。
"""

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


DEFAULT_MAX_BYTES = 1_048_576


def validate_options(options: dict) -> None:
    validate_keys(options, {"max_bytes"})
    limit = options.get("max_bytes", DEFAULT_MAX_BYTES)
    if type(limit) is not int or limit < 0:
        raise ValueError("max_bytes 必须是非负整数，单位为字节")


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    limit = options.get("max_bytes", DEFAULT_MAX_BYTES)
    errors = []
    for asset in sorted(context.snapshot.assets, key=lambda item: item.path):
        if type(asset.size) is not int or asset.size < 0:
            errors.append(f"{asset.path}：资源大小缺失或无效（{asset.size!r}）")
        elif asset.size > limit:
            errors.append(f"{asset.path}：{asset.size} 字节，超过阈值 {limit} 字节")
    return ScanResult(not errors, "\n".join(errors))
