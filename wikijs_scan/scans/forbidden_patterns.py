"""以正则表达式扫描页面原始全文中的禁止词语和格式。

流程：先编译配置中的 Python 正则，再扫描全部页面正文；默认大小写敏感，
包括代码块、注释及行内代码，不对正文做 Markdown 渲染。每条规则报告
全部非重叠匹配，并保留来源文件、起始行列、规则和有限长度的匹配预览。
任何匹配即失败；无匹配成功。无效正则在配置验证时直接报错。
"""

from bisect import bisect_right
import re

from ..models import ScanContext, ScanResult
from ._validation import validate_keys


DEFAULT_PATTERNS = ("DOKU2MD_REVIEW", r"\[\[[\s\S]*?\]\]")
PREVIEW_LENGTH = 160


def _compile_patterns(options: dict) -> list[re.Pattern[str]]:
    validate_keys(options, {"patterns"})
    patterns = options.get("patterns", list(DEFAULT_PATTERNS))
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
        raise ValueError("patterns 必须是非空正则字符串组成的数组")
    compiled = []
    for index, pattern in enumerate(patterns, 1):
        try:
            compiled.append(re.compile(pattern))
        except (re.error, OverflowError) as exc:
            raise ValueError(f"patterns 第 {index} 条正则无效：{exc}") from exc
    return compiled


def validate_options(options: dict) -> None:
    _compile_patterns(options)


def run(context: ScanContext, options: dict) -> ScanResult:
    patterns = _compile_patterns(options)
    errors = []
    for page in sorted(context.snapshot.pages, key=lambda item: item.path):
        # 保留原始正文，行号同时兼容 LF、CRLF 和 CR。
        line_starts = [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", page.content))]
        matches = [
            (match.start(), index, match.group())
            for index, pattern in enumerate(patterns, 1)
            for match in pattern.finditer(page.content)
        ]
        for start, index, matched in sorted(matches):
            line = bisect_right(line_starts, start)
            column = start - line_starts[line - 1] + 1
            preview = repr(matched[:PREVIEW_LENGTH])
            if len(matched) > PREVIEW_LENGTH:
                preview += f"（共 {len(matched)} 个字符，预览已截断）"
            errors.append(f"{page.path}:{line}:{column} [规则 {index}] {preview}")
    return ScanResult(not errors, "\n".join(errors))
