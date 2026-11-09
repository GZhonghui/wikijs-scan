"""验证四个 scan 的职责分离、独立过滤和允许环的弱连通语义。"""

import contextlib
import io
import unittest

from wikijs_scan.filtering import filter_snapshot
from wikijs_scan.models import Asset, Folder, Page, ScanContext, Snapshot
from wikijs_scan.scans import connectivity, orphan_files, reference_format, reference_targets


def page(path, content=""):
    return Page(path, path.rsplit("/", 1)[-1].removesuffix(".md"), content)


class ReferenceScansTests(unittest.TestCase):
    def test_missing_target_fails_only_existence_scan(self):
        context = ScanContext(Snapshot(pages=(page("/home.md", "[x](/missing)"),)))
        self.assertTrue(reference_format.run(context, {}).success)
        missing = reference_targets.run(context, {})
        self.assertFalse(missing.success)
        self.assertIn("/home.md:1", missing.log_text)
        self.assertIn("/missing.md", missing.log_text)
        # 不存在的目标不成为图中的虚构节点；一个实际节点的图是连通的。
        self.assertTrue(connectivity.run(context, {}).success)
        self.assertFalse(orphan_files.run(context, {}).success)

    def test_self_reference_fails_format_even_without_target_lookup(self):
        context = ScanContext(Snapshot(pages=(page("/home.md", "[self](/home)"),)))
        result = reference_format.run(context, {})
        self.assertFalse(result.success)
        self.assertIn("自引用", result.log_text)

    def test_cycles_are_valid_and_have_no_orphans(self):
        context = ScanContext(Snapshot(pages=(page("/a.md", "[b](/b)"), page("/b.md", "[a](/a)"))))
        for scan in (reference_format, reference_targets, orphan_files, connectivity):
            with self.subTest(scan=scan.__name__):
                result = scan.run(context, {})
                self.assertTrue(result.success)
                self.assertEqual(result.log_text, "")

    def test_shared_resource_connects_pages_but_not_their_incoming_counts(self):
        context = ScanContext(Snapshot(
            pages=(page("/a.md", "![](/images/shared.png)"), page("/b.md", "![](/images/shared.png)")),
            assets=(Asset("/images/shared.png", 100),)))
        self.assertTrue(connectivity.run(context, {}).success)
        result = orphan_files.run(context, {})
        self.assertFalse(result.success)
        self.assertIn("/a.md", result.log_text)
        self.assertIn("/b.md", result.log_text)
        self.assertNotIn("shared.png", result.log_text)

    def test_disconnected_cycles_and_isolated_assets_are_all_graph_nodes(self):
        context = ScanContext(Snapshot(
            pages=(page("/a.md", "[b](/b)"), page("/b.md", "[a](/a)"),
                   page("/c.md", "[d](/d)"), page("/d.md", "[c](/c)")),
            assets=(Asset("/lonely.pdf", 3),)))
        result = connectivity.run(context, {})
        self.assertFalse(result.success)
        self.assertEqual(result.log_text, "弱连通分量数量：3\n代表节点：/a.md\n代表节点：/c.md\n代表节点：/lonely.pdf")

    def test_filtered_target_disappears_without_changing_original_snapshot(self):
        original = Snapshot(pages=(page("/a.md", "[b](/b)"), page("/b.md", "[a](/a)")))
        filtered = ScanContext(filter_snapshot(original, ("/b.md",)))
        self.assertFalse(reference_targets.run(filtered, {}).success)
        self.assertTrue(reference_targets.run(ScanContext(original), {}).success)
        self.assertFalse(orphan_files.run(filtered, {}).success)
        self.assertTrue(orphan_files.run(ScanContext(original), {}).success)

    def test_directories_are_not_reference_targets_or_graph_nodes(self):
        context = ScanContext(Snapshot(pages=(page("/a.md", "![](/images)"),),
                                       folders=(Folder("/images/", "images"),)))
        self.assertFalse(reference_targets.run(context, {}).success)
        self.assertTrue(connectivity.run(context, {}).success)

    def test_empty_graph_fails_connectivity_but_has_no_orphans(self):
        context = ScanContext(Snapshot())
        self.assertFalse(connectivity.run(context, {}).success)
        self.assertIn("数量：0", connectivity.run(context, {}).log_text)
        self.assertTrue(orphan_files.run(context, {}).success)

    def test_parse_errors_cannot_be_reported_as_success(self):
        context = ScanContext(Snapshot(pages=(page("/a.md", "::block-links-grid\n```yaml\n- url: [bad\n```\n::"),)))
        for scan in (reference_format, reference_targets, orphan_files, connectivity):
            with self.subTest(scan=scan.__name__):
                result = scan.run(context, {})
                self.assertFalse(result.success)
                self.assertIn("YAML", result.log_text)

    def test_block_links_and_gallery_contribute_to_references(self):
        context = ScanContext(Snapshot(
            pages=(page("/a.md", "::block-links-grid\n```yaml\n- url: /b\n```\n::"),
                   page("/b.md", "[a](/a)\n::block-gallery\n/_files/img.png\n::")),
            assets=(Asset("/img.png", 100),)))
        self.assertTrue(orphan_files.run(context, {}).success)
        self.assertTrue(connectivity.run(context, {}).success)

    def test_custom_files_page_rule_is_used_by_scans(self):
        context = ScanContext(Snapshot(pages=(page("/a.md", "[下载](/_files/docs/manual)"),
                                              page("/docs/manual.md"))))
        self.assertTrue(reference_targets.run(context, {}).success)
        self.assertTrue(connectivity.run(context, {}).success)

    def test_scans_are_silent(self):
        output = io.StringIO()
        context = ScanContext(Snapshot(pages=(page("/a.md", "[bad](relative)"),)))
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            for scan in (reference_format, reference_targets, orphan_files, connectivity):
                scan.run(context, {})
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
