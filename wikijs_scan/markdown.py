"""从正文提取本项目规定的四种引用，保留来源行号，不判断目标是否存在。

普通 []() / ![]() 使用 CommonMark 解析，跳过引用式、自动链接、HTML 属性、
注释、行内代码及普通代码块。Wiki block 使用容器规则识别，避免把代码示例
里的 ::block-* 当成实际 block；容器可以嵌套，行号始终对应原始正文。

gallery 读取首个代码块，否则读取可见正文地址和 Markdown 图片；links-grid
读取首个代码块中的 YAML 列表，只提取每项 url。解析失败作为诊断返回，不
把未能解析的内容当成空引用。YAML 只构造安全节点，不执行自定义对象标签。

参考 Wiki.js 3.0.2 官方源码：
https://github.com/requarks/wiki/blob/3.0.2/frontend/src/renderers/markdown.js
https://github.com/requarks/wiki/blob/3.0.2/blocks/block-gallery/component.js
https://github.com/requarks/wiki/blob/3.0.2/blocks/block-links-grid/component.js
本模块只实现上述 block 的引用提取，不是完整的 Wiki.js 渲染器。
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from markdown_it import MarkdownIt
from markdown_it.rules_inline.image import image
from markdown_it.rules_inline.link import link
from markdown_it.token import Token
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


_FRONT_MATTER = re.compile(r"\A---[ \t]*\n.*?\n(?:---|\.\.\.)[ \t]*(?:\n|$)", re.S)
_BLOCK_OPEN = re.compile(r"^(:{2,})([A-Za-z][\w-]*)(?:\{.*\})?\s*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class Reference:
    href: str
    line: int
    kind: str = "link"  # link / image / gallery / grid
    in_block: bool = False


@dataclass(frozen=True)
class ParseIssue:
    line: int
    message: str


@dataclass(frozen=True)
class ParsedMarkdown:
    references: tuple[Reference, ...]
    issues: tuple[ParseIssue, ...]


def _positioned(rule, token_type):
    """包装内联规则，仅记录自身 token，保留嵌套图片已有的位置。"""
    def parse(state, silent):
        start, before = state.pos, len(state.tokens)
        matched = rule(state, silent)
        if matched and not silent:
            for token in state.tokens[before:]:
                if token.type == token_type:
                    token.meta["line_offset"] = state.src.count("\n", 0, start)
                    break
        return matched

    return parse


def _component(state, start_line, end_line, silent):
    """识别 MDC 的多行容器；关闭标记长度必须与打开标记相同。

    交给 MarkdownIt 递归解析容器内容，因此列表、引用块和代码块仍保有原来的
    语义。扫描结束标记时跨过 fenced code，避免 YAML 中的 :: 提前关闭容器。
    与 MDC 一样，未显式关闭的容器在所属上下文结束时自动关闭。
    """
    if state.sCount[start_line] - state.blkIndent >= 4:
        return False
    line = state.src[state.bMarks[start_line] + state.tShift[start_line]:state.eMarks[start_line]]
    opened = _BLOCK_OPEN.fullmatch(line)
    if not opened:
        return False
    if silent:
        return True

    marker, name = opened.groups()
    depth = 0
    fence = ""
    closing = end_line
    explicit_close = False
    for index in range(start_line + 1, end_line):
        start = state.bMarks[index] + state.tShift[index]
        text = state.src[start:state.eMarks[index]]
        if text and state.sCount[index] < state.blkIndent:
            closing = index
            break
        fence_match = _FENCE.match(text)
        if fence:
            if (fence_match and fence_match[1][0] == fence[0]
                    and len(fence_match[1]) >= len(fence) and not fence_match[2].strip()):
                fence = ""
            continue
        if fence_match:
            fence = fence_match[1]
            continue
        nested = _BLOCK_OPEN.fullmatch(text)
        if nested and nested[1] == marker:
            depth += 1
        elif text.strip() == marker:
            if depth:
                depth -= 1
            else:
                closing, explicit_close = index, True
                break

    token = state.push("wiki_block_open", name, 1)
    token.map = [start_line, closing]
    token.meta["body"] = state.getLines(start_line + 1, closing, state.sCount[start_line], False)
    old_parent, old_max, old_indent = state.parentType, state.lineMax, state.blkIndent
    state.parentType, state.lineMax = "wiki_block", closing
    state.blkIndent = state.sCount[start_line]
    state.md.block.tokenize(state, start_line + 1, closing)
    state.parentType, state.lineMax, state.blkIndent = old_parent, old_max, old_indent
    state.push("wiki_block_close", name, -1)
    state.line = closing + int(explicit_close)
    return True


def make_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": True, "store_labels": True})
    parser.enable(["table", "strikethrough"])
    # 本工具只提取地址，不渲染 HTML。保留所有 scheme，让格式 scan 能报告不允许
    # 的前缀，而不是让渲染器的 URL 安全过滤把这些引用悄悄吞掉。
    parser.validateLink = lambda _href: True
    parser.inline.ruler.at("link", _positioned(link, "link_open"))
    parser.inline.ruler.at("image", _positioned(image, "image"))
    parser.block.ruler.before("fence", "wiki_block", _component,
                              {"alt": ["paragraph", "reference", "blockquote", "list"]})
    return parser


def _inline_references(token: Token, in_block: bool) -> list[Reference]:
    found = []
    base_line = token.map[0] + 1 if token.map else 1
    # 不递归 image.children：图片说明中的链接样式不是一个可点击的实际引用。
    for child in token.children or []:
        if (child.type not in {"link_open", "image"} or "label" in child.meta
                or "line_offset" not in child.meta):
            continue
        href = child.attrGet("src" if child.type == "image" else "href")
        if href is not None:
            found.append(Reference(href, base_line + child.meta["line_offset"],
                                   "image" if child.type == "image" else "link", in_block))
    return found


def _first_code_block(tokens: list[Token]) -> Token | None:
    # 官方组件查找第一个 <pre>，因此缩进代码块与 fenced code 同样有效。
    return next((token for token in tokens if token.type in {"fence", "code_block"}), None)


def _code_start_line(token: Token) -> int:
    """fence 的 map 包括打开标记，缩进代码块的 map 则直接从正文起始。"""
    return token.map[0] + (2 if token.type == "fence" else 1)


def _grid(opened: Token, tokens: list[Token]) -> ParsedMarkdown:
    fence = _first_code_block(tokens)
    if fence is not None:
        source, base_line = fence.content, _code_start_line(fence)
    else:
        source, base_line = opened.meta["body"], opened.map[0] + 2
    if not source.strip():
        return ParsedMarkdown((), ())
    try:
        root = yaml.compose(source, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = base_line + (mark.line if mark else 0)
        problem = getattr(exc, "problem", None) or "无法解析 YAML"
        return ParsedMarkdown((), (ParseIssue(line, f"links-grid YAML 错误：{problem}"),))
    if not isinstance(root, SequenceNode) or root.tag != "tag:yaml.org,2002:seq":
        return ParsedMarkdown((), (ParseIssue(base_line, "links-grid 内容必须是 YAML 列表"),))

    found, issues = [], []
    for entry in root.value:
        line = base_line + entry.start_mark.line
        if not isinstance(entry, MappingNode) or entry.tag != "tag:yaml.org,2002:map":
            issues.append(ParseIssue(line, "links-grid 每一项必须是包含 url 的对象"))
            continue
        fields = [(key, value) for key, value in entry.value
                  if isinstance(key, ScalarNode) and key.value == "url"]
        if len(fields) != 1:
            issues.append(ParseIssue(line, "links-grid 每一项必须且只能有一个 url 字段"))
            continue
        key, value = fields[0]
        # YAML alias 复用的是定义处节点，不能把别处的 url 都报告为锚点定义行。
        value_line = base_line + max(key.start_mark.line, value.start_mark.line)
        if not isinstance(value, ScalarNode) or value.tag != "tag:yaml.org,2002:str":
            issues.append(ParseIssue(value_line, "links-grid 的 url 必须是字符串"))
            continue
        found.append(Reference(value.value.strip(), value_line, "grid", True))
    return ParsedMarkdown(tuple(found), tuple(issues))


def _gallery(tokens: list[Token]) -> list[Reference]:
    fence = _first_code_block(tokens)
    if fence is not None:
        return [Reference(match[0], _code_start_line(fence) + fence.content.count("\n", 0, match.start()),
                          "gallery", True)
                for match in re.finditer(r"\S+", fence.content)]

    found = []
    for token in tokens:
        if token.type != "inline":
            continue
        base_line = token.map[0] + 1 if token.map else 1
        line = base_line
        for child in token.children or []:
            if child.type in {"softbreak", "hardbreak"}:
                line += 1
            elif child.type == "image":
                found.append(Reference(child.attrGet("src") or "",
                                       base_line + child.meta.get("line_offset", 0), "gallery", True))
            elif child.type in {"text", "code_inline"}:
                found.extend(Reference(match[0], line + child.content.count("\n", 0, match.start()),
                                       "gallery", True)
                             for match in re.finditer(r"\S+", child.content))
                line += child.content.count("\n")
    return found


def parse_markdown(source: str, parser: MarkdownIt | None = None) -> ParsedMarkdown:
    """一次调用只读取一页正文；返回引用和解析诊断，调用之间不保留数据。"""
    source = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    front_matter = _FRONT_MATTER.match(source)
    if front_matter:
        source = "\n" * front_matter.group().count("\n") + source[front_matter.end():]
    tokens = (parser or make_parser()).parse(source)
    found, issues = [], []
    block_depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type == "wiki_block_open" and token.tag in {"block-gallery", "block-links-grid"}:
            end, depth = index + 1, 1
            while end < len(tokens) and depth:
                depth += (tokens[end].type == "wiki_block_open") - (tokens[end].type == "wiki_block_close")
                end += 1
            body = tokens[index + 1:end - 1]
            if token.tag == "block-gallery":
                found.extend(_gallery(body))
            else:
                grid = _grid(token, body)
                found.extend(grid.references)
                issues.extend(grid.issues)
            index = end
            continue
        if token.type == "wiki_block_open":
            block_depth += 1
        elif token.type == "wiki_block_close":
            block_depth -= 1
        elif token.type == "inline":
            found.extend(_inline_references(token, block_depth > 0))
        index += 1
    return ParsedMarkdown(tuple(sorted(found, key=lambda ref: ref.line)), tuple(issues))
