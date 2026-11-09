"""禁止 images/docs 目录嵌套或在它们内部建立任何子目录。

流程：将每个文件夹路径拆为完整目录名称，统计 images_dir 与 docs_dir
出现的总次数。零次合法；一次时必须出现在最后一级；超过一次非法。
因此既禁止 /a/images/sub/，也禁止 /a/images/docs/ 和 /images/images/。
任何目录违规即失败，记录全部违规路径；目录名按完整名称、区分大小写匹配。
"""

from pathlib import PurePosixPath

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


def validate_options(options: dict) -> None:
    validate_keys(options, set())


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    images = context.settings.images_dir
    docs = context.settings.docs_dir
    errors = []
    for folder in sorted(context.snapshot.folders, key=lambda item: item.path):
        parts = PurePosixPath(folder.path).parts[1:]
        count = parts.count(images) + parts.count(docs)
        if count > 1:
            errors.append(f"{folder.path}：资源目录名称共出现 {count} 次，不允许嵌套")
        elif count == 1 and parts[-1] not in {images, docs}:
            errors.append(f"{folder.path}：资源目录下面不允许建立子目录")
    return ScanResult(not errors, "\n".join(errors))
