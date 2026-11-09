"""统计目录与文件：统计数量，并找出空目录和无后缀文件。

流程：读取当前快照的目录与文件路径，以最后一个后缀（忽略大小写）统计
文件数量；通过文件和目录的直接父目录找出没有任何直接子项的空目录。
根目录不在快照的 folders 中，不计入数量，也不作为空目录报告。
有空目录或无后缀文件即失败，否则成功。日志包含统计与全部异常路径，
不逐个列出正常文件。本模块只返回结果，不打印、不修改快照。
"""

from collections import Counter
from pathlib import PurePosixPath

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


def validate_options(options: dict) -> None:
    validate_keys(options, set())


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    snapshot = context.snapshot
    paths = sorted(snapshot.file_paths)
    suffix_counts = Counter(PurePosixPath(path).suffix.lower() for path in paths)
    occupied = {str(PurePosixPath(path).parent) for path in paths}
    occupied.update(str(PurePosixPath(folder.path).parent) for folder in snapshot.folders)
    empty_folders = sorted(
        folder.path for folder in snapshot.folders
        if folder.path.rstrip("/") not in occupied
    )
    extensionless = [path for path in paths if not PurePosixPath(path).suffix]

    lines = [
        f"文件夹数量：{len(snapshot.folders)}",
        f"空文件夹数量：{len(empty_folders)}",
        f"文件数量：{len(paths)}",
        "按后缀统计：",
    ]
    lines.extend(f"  {suffix or '无后缀'}：{count}" for suffix, count in sorted(suffix_counts.items()))
    if not suffix_counts:
        lines.append("  （无文件）")
    if empty_folders:
        lines.extend(["", "空文件夹：", *empty_folders])
    if extensionless:
        lines.extend(["", "无后缀文件：", *extensionless])
    return ScanResult(not empty_folders and not extensionless, "\n".join(lines))
