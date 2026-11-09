"""检查每个普通文件夹是否有对应的同名 Markdown 页面。

流程：遍历当前快照的文件夹，跳过末级目录名精确等于全局 images_dir 或
docs_dir 的目录，然后检查 /a/b/ 对应的 /a/b.md 是否存在于页面集合中。
这里只验证存在性，不读取该页面内容；同名资源不能替代页面。
任何文件夹缺少对应页面即失败，记录全部目录与预期页面路径。
"""

from pathlib import PurePosixPath

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


def validate_options(options: dict) -> None:
    validate_keys(options, set())


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    pages = {page.path for page in context.snapshot.pages}
    exempt = {context.settings.images_dir, context.settings.docs_dir}
    errors = []
    for folder in sorted(context.snapshot.folders, key=lambda item: item.path):
        if PurePosixPath(folder.path).name in exempt:
            continue
        expected = folder.path.rstrip("/") + ".md"
        if expected not in pages:
            errors.append(f"{folder.path}：缺少对应页面 {expected}")
    return ScanResult(not errors, "\n".join(errors))
