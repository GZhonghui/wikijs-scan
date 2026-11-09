"""检查页面与文件夹的 title 是否等于路径末级名称，且名称符合字符限制。

流程：页面名称只去掉扫描器补上的最后一个 .md，文件夹取最后一级名称；
随后分别验证 title 和名称都完全匹配 [a-z0-9-]+，并验证二者完全相等。
任何条件不满足即失败，记录全部异常页面、文件夹及原因。资源不参与。
"""

from pathlib import PurePosixPath
import re

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


NAME_PATTERN = re.compile(r"[a-z0-9-]+")


def validate_options(options: dict) -> None:
    validate_keys(options, set())


def run(context: ScanContext, options: dict) -> ScanResult:
    validate_options(options)
    entries = [
        (page.path, page.title, PurePosixPath(page.path).name.removesuffix(".md"))
        for page in context.snapshot.pages
    ]
    entries.extend(
        (folder.path, folder.title, PurePosixPath(folder.path).name)
        for folder in context.snapshot.folders
    )
    errors = []
    for path, title, name in sorted(entries):
        reasons = []
        if title != name:
            reasons.append(f"title={title!r}，应为 {name!r}")
        if not NAME_PATTERN.fullmatch(name):
            reasons.append("路径末级名称必须只含小写英文字母、数字和连字符，且不能为空")
        if not NAME_PATTERN.fullmatch(title):
            reasons.append("title 必须只含小写英文字母、数字和连字符，且不能为空")
        if reasons:
            errors.append(f"{path}：{'；'.join(reasons)}")
    return ScanResult(not errors, "\n".join(errors))
