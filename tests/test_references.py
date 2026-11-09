"""四类引用的真实语法、来源位置及用户自定义路径语义回归测试。"""

import unittest

from wikijs_scan.markdown import Reference, parse_markdown
from wikijs_scan.references import resolve_reference


class MarkdownTests(unittest.TestCase):
    def test_all_four_forms_keep_original_line_numbers(self):
        parsed = parse_markdown('''# 标题

[页](/target) ![图](/images/a.png)
::block-gallery{unlockAspectRatio="true"}
/_files/images/b.png
/_files/images/c.png
::
::block-links-grid{maxWidth="360px"}
```yaml
- title: 第一页
  url: /a
- title: 第二页
  url: /b
```
::
''')
        self.assertFalse(parsed.issues)
        self.assertEqual([(ref.href, ref.line, ref.kind, ref.in_block) for ref in parsed.references], [
            ("/target", 3, "link", False), ("/images/a.png", 3, "image", False),
            ("/_files/images/b.png", 5, "gallery", True),
            ("/_files/images/c.png", 6, "gallery", True),
            ("/a", 11, "grid", True), ("/b", 13, "grid", True),
        ])

    def test_examples_comments_inline_code_reference_links_are_ignored(self):
        parsed = parse_markdown('''`[code](/skip)`
<!-- [comment](/skip) -->

    [indented](/skip)

````markdown
::block-links-grid
```yaml
- url: /skip
```
::
````

[reference][label]
![reference-image][label]
[label]: /skip
<a href="/skip">x</a>
<https://example.org/[text](/skip)>
[[not-a-link]]
[real](/keep)
''')
        self.assertEqual([ref.href for ref in parsed.references], ["/keep"])

    def test_nested_image_and_alt_text(self):
        parsed = parse_markdown('[![image](/pic.png)](/page)\n![alt [fake](/ignore)](/second.png)')
        self.assertEqual([ref.href for ref in parsed.references], ["/page", "/pic.png", "/second.png"])

    def test_multiline_link_and_frontmatter_lines(self):
        parsed = parse_markdown('---\ntitle: "[fake](/skip)"\n---\n[a\n![b](/pic.png)](/page)\n')
        self.assertEqual([(ref.href, ref.line) for ref in parsed.references], [("/page", 4), ("/pic.png", 5)])

    def test_inline_link_inside_generic_nested_blocks_is_in_block(self):
        parsed = parse_markdown('''::block-tabs
::block-tab{title="x"}
[a](./a)
::
::block-links-grid
```yaml
- url: /b
```
::
::
[c](./c)
''')
        self.assertEqual([(ref.href, ref.in_block) for ref in parsed.references],
                         [("./a", True), ("/b", True), ("./c", False)])

    def test_code_fence_cannot_close_container(self):
        parsed = parse_markdown('''::block-links-grid
```yaml
- title: |
    ::
  url: /target
```
::
[after](/after)
''')
        self.assertFalse(parsed.issues)
        self.assertEqual([(ref.href, ref.line) for ref in parsed.references], [("/target", 5), ("/after", 8)])

    def test_gallery_reads_first_fence_and_ignores_surrounding_text(self):
        parsed = parse_markdown('''::block-gallery
/ignored.png
```text
/first.png /second.png
```
```text
/ignored-too.png
```
::
''')
        self.assertEqual([(ref.href, ref.line) for ref in parsed.references],
                         [("/first.png", 4), ("/second.png", 4)])

    def test_gallery_accepts_images_and_bare_addresses(self):
        parsed = parse_markdown('''::block-gallery
/_files/a.png
![caption](/_files/b.png)
::
''')
        self.assertEqual([ref.href for ref in parsed.references], ["/_files/a.png", "/_files/b.png"])

    def test_grid_does_not_parse_markdown_inside_other_fields(self):
        parsed = parse_markdown('''::block-links-grid
```yaml
- title: '[not a link](/skip)'
  url: '/correct'
  image: /not-in-scope.png
  icon: img:/not-in-scope-either.png
```
::
''')
        self.assertEqual([ref.href for ref in parsed.references], ["/correct"])

    def test_bad_yaml_reports_actual_line(self):
        parsed = parse_markdown('''# Title
::block-links-grid
```yaml
- url: [broken
```
::
''')
        self.assertTrue(parsed.issues)
        self.assertGreaterEqual(parsed.issues[0].line, 4)
        self.assertFalse(parsed.references)

    def test_grid_schema_errors_and_unsafe_tags_are_reported(self):
        for content in ["url: /x", "- 42", "- title: no-url", "- url: 42",
                        "- url: /x\n  url: /y", "!!python/object:os.system [echo bad]"]:
            with self.subTest(content=content):
                parsed = parse_markdown(f"::block-links-grid\n```yaml\n{content}\n```\n::\n")
                self.assertTrue(parsed.issues)

    def test_empty_grid_url_is_preserved_for_self_reference_diagnostic(self):
        parsed = parse_markdown("::block-links-grid\n```yaml\n- url: ''\n```\n::\n")
        self.assertEqual(parsed.references[0].href, "")

    def test_unclosed_container_follows_mdc_end_of_context(self):
        parsed = parse_markdown("::block-gallery\n/a.png\n/b.png")
        self.assertEqual([ref.href for ref in parsed.references], ["/a.png", "/b.png"])

    def test_url_schemes_are_not_silently_removed_by_renderer_safety(self):
        parsed = parse_markdown("[bad](javascript:alert(1)) [file](file:///a)")
        self.assertEqual([ref.href for ref in parsed.references], ["javascript:alert(1)", "file:///a"])

    def test_list_table_and_blockquote_keep_exact_lines(self):
        parsed = parse_markdown(
            "- 列表\n    [续行](/list.md)\n\n"
            "| 名称 | 链接 |\n| --- | --- |\n| 文档 | [表格](/table.md) |\n\n"
            "> 引用\n> [引用内](/quote.md)\n")
        self.assertEqual([(ref.href, ref.line) for ref in parsed.references],
                         [("/list.md", 2), ("/table.md", 6), ("/quote.md", 9)])

    def test_multiline_nested_images_inside_list_and_quote_keep_exact_lines(self):
        parsed = parse_markdown(
            "# 标题\n\n- 项目\n\n  [外层\n  ![图片](/list.png)](/list)\n\n"
            "> [外层\n> ![图片](/quote.png)](/quote)\n")
        self.assertEqual([(ref.href, ref.line) for ref in parsed.references],
                         [("/list", 5), ("/list.png", 6), ("/quote", 8), ("/quote.png", 9)])

    def test_block_in_blockquote_keeps_yaml_line(self):
        parsed = parse_markdown(
            "# 标题\n\n> ::block-links-grid\n> ```yaml\n> - url: /a\n> ```\n> ::\n")
        self.assertEqual(parsed.references, (Reference("/a", 5, "grid", True),))

    def test_indented_code_in_gallery_is_read_as_component_data(self):
        parsed = parse_markdown("::block-gallery\n\n    /a.png\n    /b.png\n\n::\n")
        self.assertEqual(parsed.references, (Reference("/a.png", 3, "gallery", True),
                                            Reference("/b.png", 4, "gallery", True)))

    def test_indented_code_in_grid_preserves_data_and_line(self):
        parsed = parse_markdown("::block-links-grid\n\n    - url: /a\n    - url: /b\n\n::\n")
        self.assertFalse(parsed.issues)
        self.assertEqual(parsed.references, (Reference("/a", 3, "grid", True),
                                            Reference("/b", 4, "grid", True)))

    def test_yaml_alias_uses_reference_line_instead_of_anchor_definition(self):
        parsed = parse_markdown(
            "::block-links-grid\n```yaml\n- url: &target /a\n- url: *target\n```\n::\n")
        self.assertFalse(parsed.issues)
        self.assertEqual([ref.line for ref in parsed.references], [3, 4])

    def test_comments_only_grid_is_not_silently_treated_as_valid_yaml_list(self):
        parsed = parse_markdown("::block-links-grid\n```yaml\n# comment\n```\n::\n")
        self.assertTrue(parsed.issues)

    def test_empty_grid_and_explicit_empty_list_have_no_references(self):
        for body in ("", "[]"):
            with self.subTest(body=body):
                parsed = parse_markdown(f"::block-links-grid\n```yaml\n{body}\n```\n::\n")
                self.assertEqual(parsed.references, ())
                self.assertEqual(parsed.issues, ())

    def test_comment_cannot_create_component_or_links(self):
        parsed = parse_markdown("<!--\n::block-gallery\n/fake.png\n::\n-->\n[real](/real)")
        self.assertEqual([ref.href for ref in parsed.references], ["/real"])

    def test_escaped_examples_and_quoted_code_fences_are_not_references(self):
        parsed = parse_markdown(
            "\\[x](/fake) \\!\\[x](/fake.png)\n\n"
            "> ```md\n> ::block-gallery\n> /fake.png\n> ::\n> ```\n\n"
            "    ::block-gallery\n    /also-fake.png\n    ::\n")
        self.assertEqual(parsed.references, ())

    def test_reference_link_can_contain_real_inline_image(self):
        parsed = parse_markdown("[![图片](/image.png)][id]\n\n[id]: /ignored.md\n")
        self.assertEqual(parsed.references, (Reference("/image.png", 1, "image"),))

    def test_parser_does_not_leak_reference_definitions_between_pages(self):
        from wikijs_scan.markdown import make_parser
        parser = make_parser()
        self.assertEqual(parse_markdown("[x]: /old\n", parser).references, ())
        self.assertEqual(parse_markdown("[x] [real](/new)\n", parser).references,
                         (Reference("/new", 1),))

    def test_bom_windows_newlines_and_frontmatter_end_marker(self):
        parsed = parse_markdown("\ufeff---\r\ntitle: '[忽略](/ignored)'\r\n...\r\n[正文](/real.md)\r\n")
        self.assertEqual(parsed.references, (Reference("/real.md", 4),))


class ResolverTests(unittest.TestCase):
    def resolve(self, href, kind="link", in_block=False):
        return resolve_reference(Reference(href, 1, kind, in_block), "/a/current.md")

    def test_user_custom_files_prefix_rule_is_not_original_wiki_routing(self):
        for kind in ("link", "grid"):
            with self.subTest(kind=kind):
                self.assertEqual(self.resolve("/_files/docs/manual", kind).target, "/docs/manual.md")
        for kind in ("image", "gallery"):
            with self.subTest(kind=kind):
                self.assertEqual(self.resolve("/_files/docs/manual", kind).target, "/docs/manual")

    def test_existing_suffix_is_retained_for_all_link_types(self):
        for kind in ("link", "grid", "image", "gallery"):
            with self.subTest(kind=kind):
                self.assertEqual(self.resolve("/_files/docs/manual.pdf", kind).target, "/docs/manual.pdf")

    def test_relative_parent_resolution_and_normalization(self):
        self.assertEqual(self.resolve("./next").target, "/a/next.md")
        self.assertEqual(self.resolve("./../docs/paper.pdf").target, "/docs/paper.pdf")
        self.assertEqual(self.resolve("/a/../b").target, "/b.md")

    def test_query_anchor_and_encoded_filename(self):
        self.assertEqual(self.resolve("/target?q=1#section").target, "/target.md")
        self.assertEqual(self.resolve("/_files/a%20b%23c.pdf#section").target, "/a b#c.pdf")
        self.assertEqual(self.resolve("/a+b.pdf").target, "/a+b.pdf")

    def test_all_self_references_are_errors(self):
        for href in ("", "#section", "?query=1", "/a/current", "/a/current.md", "./current",
                     "./current.md#section", "/a/../a/current"):
            with self.subTest(href=href):
                self.assertIn("自引用", self.resolve(href).error or "")

    def test_block_relative_is_rejected_before_self_reference(self):
        self.assertIn("block", self.resolve("./current", "link", True).error or "")
        self.assertIn("block", self.resolve("./other", "grid").error or "")
        self.assertIn("block", self.resolve("./picture.png", "gallery").error or "")

    def test_bare_and_parent_relative_paths_are_invalid(self):
        for href in ("target", "../target", "ftp://example.org/a", "file:///a"):
            with self.subTest(href=href):
                self.assertIsNotNone(self.resolve(href).error)

    def test_external_prefixes_are_literal_and_configurable(self):
        self.assertTrue(self.resolve("https://example.org/a").skipped)
        self.assertTrue(self.resolve("mailto:user@example.org").skipped)
        self.assertTrue(self.resolve("http-custom-prefix").skipped)
        self.assertFalse(self.resolve("HTTP://example.org").skipped)
        self.assertTrue(resolve_reference(Reference("tel:123", 1), "/a.md", ("tel:",)).skipped)

    def test_target_existence_is_not_part_of_format_validation(self):
        resolution = self.resolve("/does-not-exist")
        self.assertEqual(resolution.target, "/does-not-exist.md")
        self.assertIsNone(resolution.error)


if __name__ == "__main__":
    unittest.main()
