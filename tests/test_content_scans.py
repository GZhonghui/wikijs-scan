"""非引用 scan 的行为测试：以需求中的正反例验证边界，不访问数据库。"""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest

from wikijs_scan.models import Asset, Folder, GlobalSettings, Page, ScanContext, Snapshot
from wikijs_scan.scans import (
    duplicate_sizes,
    folder_index,
    forbidden_patterns,
    inventory,
    naming,
    resource_folders,
    resource_location,
    resource_size,
)


def context(*, pages=(), assets=(), folders=(), settings=None):
    return ScanContext(
        Snapshot(pages=tuple(pages), assets=tuple(assets), folders=tuple(folders)),
        settings or GlobalSettings(),
    )


class InventoryTests(unittest.TestCase):
    def test_counts_last_suffix_case_insensitively_without_listing_normal_files(self):
        result = inventory.run(context(
            pages=[Page("/home.md", "home", "")],
            assets=[Asset("/images/one.PNG", 1), Asset("/images/two.png", 2), Asset("/docs/a.tar.gz", 3)],
            folders=[Folder("/images/", "images"), Folder("/docs/", "docs")],
        ), {})
        self.assertTrue(result.success)
        self.assertIn("文件夹数量：2", result.log_text)
        self.assertIn("空文件夹数量：0", result.log_text)
        self.assertIn(".png：2", result.log_text)
        self.assertIn(".gz：1", result.log_text)
        self.assertNotIn("/images/one.PNG", result.log_text)

    def test_empty_child_is_reported_but_its_parent_is_not_empty(self):
        result = inventory.run(context(folders=[
            Folder("/a/", "a"), Folder("/a/empty/", "empty"),
        ]), {})
        self.assertFalse(result.success)
        self.assertIn("空文件夹数量：1", result.log_text)
        self.assertIn("\n/a/empty/", result.log_text)
        self.assertNotIn("\n/a/\n", result.log_text)

    def test_extensionless_files_fail_even_without_empty_folders(self):
        result = inventory.run(context(assets=[Asset("/readme", 4), Asset("/.hidden", 5)]), {})
        self.assertFalse(result.success)
        self.assertIn("无后缀：2", result.log_text)
        self.assertIn("/readme", result.log_text)
        self.assertIn("/.hidden", result.log_text)

    def test_empty_snapshot_does_not_invent_root_folder(self):
        result = inventory.run(context(), {})
        self.assertTrue(result.success)
        self.assertIn("文件夹数量：0", result.log_text)


class ResourceLocationTests(unittest.TestCase):
    def test_direct_parent_and_full_directory_name_are_required(self):
        result = resource_location.run(context(
            pages=[Page("/arbitrary/page.md", "page", "")],
            assets=[
                Asset("/a/images/good.PNG", 1),
                Asset("/a/docs/good.pdf", 2),
                Asset("/a/images/sub/bad.png", 3),
                Asset("/a/myimages/bad.jpg", 4),
                Asset("/a/images/bad.pdf", 5),
            ],
        ), {})
        self.assertFalse(result.success)
        self.assertIn("/a/images/sub/bad.png", result.log_text)
        self.assertIn("/a/myimages/bad.jpg", result.log_text)
        self.assertIn("/a/images/bad.pdf", result.log_text)
        self.assertNotIn("good", result.log_text)
        self.assertNotIn("page.md", result.log_text)

    def test_custom_directories_and_extension_list(self):
        result = resource_location.run(context(
            assets=[Asset("/a/pictures/a.AVIF", 1), Asset("/a/files/b.png", 2)],
            settings=GlobalSettings(images_dir="pictures", docs_dir="files"),
        ), {"image_extensions": ["AVIF"]})
        self.assertTrue(result.success)
        self.assertEqual(result.log_text, "")

    def test_invalid_extension_list_is_rejected(self):
        for value in ("png", [".png"], [""], [7], ["a/b"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resource_location.validate_options({"image_extensions": value})


class ResourceSizeTests(unittest.TestCase):
    def test_boundary_equal_is_allowed_and_only_larger_asset_is_reported(self):
        result = resource_size.run(context(
            pages=[Page("/large.md", "large", "x" * 100)],
            assets=[Asset("/equal.bin", 10), Asset("/large.bin", 11), Asset("/empty.bin", 0)],
        ), {"max_bytes": 10})
        self.assertFalse(result.success)
        self.assertIn("/large.bin", result.log_text)
        self.assertIn("11 字节", result.log_text)
        self.assertNotIn("/equal.bin", result.log_text)
        self.assertNotIn("/empty.bin", result.log_text)
        self.assertNotIn("/large.md", result.log_text)

    def test_default_is_one_mebibyte(self):
        result = resource_size.run(context(assets=[Asset("/limit.bin", 1_048_576)]), {})
        self.assertTrue(result.success)

    def test_missing_or_invalid_size_never_passes_as_zero(self):
        result = resource_size.run(context(assets=[
            Asset("/missing.bin", None), Asset("/negative.bin", -1), Asset("/bool.bin", True),
        ]), {})
        self.assertFalse(result.success)
        for path in ("/missing.bin", "/negative.bin", "/bool.bin"):
            self.assertIn(path, result.log_text)

    def test_invalid_threshold_is_rejected(self):
        for value in (-1, True, 0.5, "10", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resource_size.validate_options({"max_bytes": value})


class DuplicateSizesTests(unittest.TestCase):
    def test_all_equal_sizes_are_grouped_regardless_of_names_or_types(self):
        result = duplicate_sizes.run(context(assets=[
            Asset("/b.jpg", 12), Asset("/a.pdf", 12), Asset("/unique.png", 13),
            Asset("/zero-a.txt", 0), Asset("/zero-b.bin", 0),
        ]), {})
        self.assertFalse(result.success)
        self.assertIn("12 字节（2 个资源）", result.log_text)
        self.assertIn("/a.pdf\n/b.jpg", result.log_text)
        self.assertIn("0 字节（2 个资源）", result.log_text)
        self.assertNotIn("/unique.png", result.log_text)

    def test_unknown_sizes_are_errors_not_duplicate_zeroes(self):
        result = duplicate_sizes.run(context(assets=[
            Asset("/missing-a.bin", None), Asset("/missing-b.bin", None), Asset("/zero.bin", 0),
        ]), {})
        self.assertFalse(result.success)
        self.assertIn("/missing-a.bin", result.log_text)
        self.assertIn("/missing-b.bin", result.log_text)
        self.assertNotIn("0 字节（", result.log_text)

    def test_unique_sizes_are_silent(self):
        result = duplicate_sizes.run(context(assets=[Asset("/a.bin", 1), Asset("/b.bin", 2)]), {})
        self.assertTrue(result.success)
        self.assertEqual(result.log_text, "")


class NamingTests(unittest.TestCase):
    def test_valid_page_and_folder_names_with_resources_ignored(self):
        result = naming.run(context(
            pages=[Page("/a/hello-c2.md", "hello-c2", "")],
            folders=[Folder("/a/", "a"), Folder("/a/hello-c2/", "hello-c2")],
            assets=[Asset("/资源_图片.PNG", 1)],
        ), {})
        self.assertTrue(result.success)
        self.assertEqual(result.log_text, "")

    def test_title_mismatch_and_disallowed_characters_are_all_reported(self):
        result = naming.run(context(
            pages=[
                Page("/hello.md", "other", ""),
                Page("/Upper.md", "Upper", ""),
                Page("/hello_world.md", "hello_world", ""),
                Page("/hello.v1.md", "hello.v1", ""),
            ],
            folders=[Folder("/目录/", "目录"), Folder("/valid/", "")],
        ), {})
        self.assertFalse(result.success)
        for path in ("/hello.md", "/Upper.md", "/hello_world.md", "/hello.v1.md", "/目录/", "/valid/"):
            self.assertIn(path, result.log_text)


class ForbiddenPatternsTests(unittest.TestCase):
    def test_default_rules_scan_code_and_comments_case_sensitively(self):
        content = "doku2md_review\n```\nDOKU2MD_REVIEW\n```\n<!-- [[todo]] -->\n"
        result = forbidden_patterns.run(context(pages=[Page("/a.md", "a", content)]), {})
        self.assertFalse(result.success)
        self.assertIn("/a.md:3:1", result.log_text)
        self.assertIn("/a.md:5:6", result.log_text)
        self.assertNotIn("/a.md:1:", result.log_text)

    def test_multiline_match_reports_start_line_and_handles_all_newline_styles(self):
        content = "first\r\nsecond\r[[one\ntwo]]"
        result = forbidden_patterns.run(context(pages=[Page("/a.md", "a", content)]), {})
        self.assertFalse(result.success)
        self.assertIn("/a.md:3:1", result.log_text)
        self.assertIn("[[one\\ntwo]]", result.log_text)

    def test_custom_patterns_find_all_matches_with_bounded_preview(self):
        content = "ABC ABC\n" + "x" * 200
        result = forbidden_patterns.run(context(pages=[Page("/a.md", "a", content)]), {
            "patterns": ["ABC", "x+"],
        })
        self.assertFalse(result.success)
        self.assertEqual(len(result.log_text.splitlines()), 3)
        self.assertIn("/a.md:1:1", result.log_text)
        self.assertIn("/a.md:1:5", result.log_text)
        self.assertIn("共 200 个字符", result.log_text)
        self.assertNotIn("x" * 161, result.log_text)

    def test_no_matches_and_explicit_empty_rule_list_succeed_silently(self):
        for content, options in (("ordinary text", {}), ("[[allowed]]", {"patterns": []})):
            with self.subTest(content=content):
                result = forbidden_patterns.run(context(pages=[Page("/a.md", "a", content)]), options)
                self.assertTrue(result.success)
                self.assertEqual(result.log_text, "")

    def test_invalid_pattern_configuration_is_rejected_before_scanning(self):
        for value in (["["], [""], [7], "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                forbidden_patterns.validate_options({"patterns": value})


class FolderIndexTests(unittest.TestCase):
    def test_exact_resource_directory_names_are_exempt(self):
        result = folder_index.run(context(
            pages=[Page("/a.md", "a", ""), Page("/a/b.md", "b", "")],
            folders=[
                Folder("/a/", "a"), Folder("/a/b/", "b"),
                Folder("/a/images/", "images"), Folder("/a/docs/", "docs"),
                Folder("/a/myimages/", "myimages"),
            ],
        ), {})
        self.assertFalse(result.success)
        self.assertIn("/a/myimages/：缺少对应页面 /a/myimages.md", result.log_text)
        self.assertEqual(len(result.log_text.splitlines()), 1)

    def test_asset_with_md_suffix_does_not_replace_page(self):
        result = folder_index.run(context(
            assets=[Asset("/a.md", 10)], folders=[Folder("/a/", "a")],
        ), {})
        self.assertFalse(result.success)
        self.assertIn("/a.md", result.log_text)

    def test_custom_directory_names_are_used(self):
        result = folder_index.run(context(
            folders=[Folder("/pictures/", "pictures"), Folder("/files/", "files")],
            settings=GlobalSettings(images_dir="pictures", docs_dir="files"),
        ), {})
        self.assertTrue(result.success)
        self.assertEqual(result.log_text, "")


class ResourceFoldersTests(unittest.TestCase):
    def test_special_directory_may_only_be_the_last_segment_once(self):
        result = resource_folders.run(context(folders=[
            Folder("/a/", "a"), Folder("/a/images/", "images"), Folder("/a/docs/", "docs"),
            Folder("/a/images/sub/", "sub"), Folder("/a/images/docs/", "docs"),
            Folder("/images/images/", "images"), Folder("/a/myimages/sub/", "sub"),
        ]), {})
        self.assertFalse(result.success)
        for path in ("/a/images/sub/", "/a/images/docs/", "/images/images/"):
            self.assertIn(path, result.log_text)
        self.assertEqual(len(result.log_text.splitlines()), 3)

    def test_custom_names_are_compared_as_complete_segments(self):
        result = resource_folders.run(context(
            folders=[Folder("/a/pics/", "pics"), Folder("/a/mypics/sub/", "sub")],
            settings=GlobalSettings(images_dir="pics", docs_dir="files"),
        ), {})
        self.assertTrue(result.success)
        self.assertEqual(result.log_text, "")


class ModuleContractTests(unittest.TestCase):
    MODULES = (
        inventory, resource_location, resource_size, duplicate_sizes,
        naming, forbidden_patterns, folder_index, resource_folders,
    )

    def test_unknown_options_and_wrong_container_types_are_rejected(self):
        for module in self.MODULES:
            for options in ({"typo": True}, [], None):
                with self.subTest(module=module.__name__, options=options), self.assertRaises(ValueError):
                    module.validate_options(options)

    def test_scans_are_silent_and_do_not_modify_shared_snapshot(self):
        scan_context = context(
            pages=[Page("/home.md", "home", "DOKU2MD_REVIEW")],
            assets=[Asset("/images/a.png", 1), Asset("/images/b.png", 1)],
            folders=[Folder("/images/", "images")],
        )
        before = repr(scan_context)
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            for module in self.MODULES:
                module.run(scan_context, {})
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(repr(scan_context), before)


if __name__ == "__main__":
    unittest.main()
