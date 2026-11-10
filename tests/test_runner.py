"""调度行为测试：前置条件、逐项隔离、过滤视图和日志输出契约。"""

from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import Mock

from main import FILTER_FORBIDDEN
from wikijs_scan.config import load_config, parse_config
from wikijs_scan.models import Asset, Folder, Page, ScanResult, Snapshot
from wikijs_scan.registry import ScanDefinition
from wikijs_scan.runner import run_scans


def configuration(*scan_ids, patches=None):
    raw = {
        "global": {"database": {}, "scan_order": list(scan_ids)},
        "scans": {scan_id: {} for scan_id in scan_ids},
    }
    for scan_id, patch in (patches or {}).items():
        raw["scans"][scan_id].update(patch)
    return parse_config(raw)


def definition(callback):
    return ScanDefinition("测试 scan", callback, lambda options: None)


def execute(snapshot, config, *, definitions=None, forbidden_filter=FILTER_FORBIDDEN):
    log, stdout = StringIO(), StringIO()
    kwargs = {} if definitions is None else {"definitions": definitions}
    report = run_scans(snapshot, config, log, stdout, forbidden_filter, **kwargs)
    return report, log.getvalue(), stdout.getvalue()


class RunnerTests(unittest.TestCase):
    def test_default_configuration_can_run_all_twelve_cases_on_one_snapshot(self):
        config = load_config(Path(__file__).resolve().parents[1] / "config.json")
        snapshot = Snapshot(pages=(
            Page("/a.md", "a", "[b](/b)"),
            Page("/b.md", "b", "[a](/a)"),
        ))
        report, log, output = execute(snapshot, config)
        self.assertEqual((report.total, report.failed, report.crashed, report.exit_code), (12, 0, 0, 0))
        self.assertEqual(output.splitlines(), [f"{spec.scan_id}：成功" for spec in config.scans])
        self.assertIn("--- inventory", log)
        self.assertIn("文件数量：2", log)
        self.assertIn("--- connectivity", log)

    def test_disabled_prerequisite_is_not_success_and_dependent_is_not_called(self):
        config = configuration("inventory", "reference_format", "resource_size", patches={
            "inventory": {"enabled": False},
            "reference_format": {"depends_on": ["inventory"]},
        })
        skipped = Mock(return_value=ScanResult(True))
        unrelated = Mock(return_value=ScanResult(True))
        report, log, output = execute(Snapshot(), config, definitions={
            "reference_format": definition(skipped), "resource_size": definition(unrelated),
        })
        skipped.assert_not_called()
        unrelated.assert_called_once()
        self.assertEqual((report.total, report.failed, report.exit_code), (2, 1, 1))
        self.assertIn("尚未执行或未启用", log)
        self.assertEqual(output, "reference_format：失败\nresource_size：成功\n")

    def test_duplicate_md5_id_reports_content_duplicates(self):
        config = configuration("duplicate_md5")
        distinct = Snapshot(assets=(Asset("/a.bin", 3, b"abc"), Asset("/b.bin", 3, b"xyz")))
        report, log, output = execute(distinct, config)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(output, "duplicate_md5：成功\n")
        self.assertIn("资源内容重复（MD5）", log)

        duplicate = Snapshot(assets=(Asset("/a.bin", 3, b"abc"), Asset("/b.bin", 3, b"abc")))
        report, log, output = execute(duplicate, config)
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(output, "duplicate_md5：失败\n")
        self.assertIn("MD5 900150983cd24fb0d6963f7d28e17f72（2 个资源）", log)
        self.assertIn("/a.bin\n/b.bin", log)

    def test_later_prerequisite_does_not_trigger_reordering_or_retry(self):
        config = configuration("reference_format", "inventory", "reference_targets", patches={
            "reference_format": {"depends_on": ["inventory"]},
            "reference_targets": {"depends_on": ["reference_format"]},
        })
        format_run = Mock(return_value=ScanResult(True))
        inventory_run = Mock(return_value=ScanResult(True))
        targets_run = Mock(return_value=ScanResult(True))
        report, log, output = execute(Snapshot(), config, definitions={
            "reference_format": definition(format_run), "inventory": definition(inventory_run),
            "reference_targets": definition(targets_run),
        })
        format_run.assert_not_called()
        targets_run.assert_not_called()
        inventory_run.assert_called_once()
        self.assertEqual(report.failed, 2)
        self.assertIn("reference_format（失败）", log)
        self.assertEqual(output.splitlines(), ["reference_format：失败", "inventory：成功", "reference_targets：失败"])

    def test_case_failure_and_exception_do_not_stop_unrelated_cases(self):
        config = configuration("inventory", "reference_format", "resource_size", "naming", patches={
            "reference_format": {"depends_on": ["inventory"]},
        })
        dependent = Mock(return_value=ScanResult(True))
        last = Mock(return_value=ScanResult(True, "最后一项正常完成"))
        report, log, output = execute(Snapshot(), config, definitions={
            "inventory": definition(lambda context, options: ScanResult(False, "目录规则失败")),
            "reference_format": definition(dependent),
            "resource_size": definition(Mock(side_effect=RuntimeError("模拟运行异常"))),
            "naming": definition(last),
        })
        dependent.assert_not_called()
        last.assert_called_once()
        self.assertEqual((report.total, report.failed, report.crashed, report.exit_code), (4, 3, 1, 2))
        self.assertIn("目录规则失败", log)
        self.assertIn("运行异常", log)
        self.assertIn("模拟运行异常", log)
        self.assertIn("最后一项正常完成", log)
        self.assertEqual(output, "inventory：失败\nreference_format：失败\nresource_size：失败\nnaming：成功\n")

    def test_enabling_forbidden_filter_fails_only_that_case_without_calling_it(self):
        config = configuration("inventory", "naming", patches={
            "inventory": {"filter": {"enabled": True, "prefixes": []}},
        })
        forbidden_run = Mock(return_value=ScanResult(True))
        allowed_run = Mock(return_value=ScanResult(True))
        report, log, output = execute(Snapshot(), config, definitions={
            "inventory": definition(forbidden_run), "naming": definition(allowed_run),
        })
        forbidden_run.assert_not_called()
        allowed_run.assert_called_once()
        self.assertEqual((report.failed, report.crashed, report.exit_code), (1, 0, 1))
        self.assertIn("未执行：本 scan 禁止过滤", log)
        self.assertEqual(output, "inventory：失败\nnaming：成功\n")

    def test_filtered_view_does_not_change_other_cases_or_original_snapshot(self):
        snapshot = Snapshot(
            pages=(Page("/home.md", "home", ""), Page("/homepage.md", "homepage", ""), Page("/keep.md", "keep", "")),
            assets=(Asset("/home/a.png", 1), Asset("/keep/a.png", 2)),
            folders=(Folder("/home/", "home"), Folder("/keep/", "keep")),
        )
        config = configuration("naming", "resource_location", patches={
            "naming": {"filter": {"enabled": True, "prefixes": ["/home"]}},
        })
        seen = []

        def observe(context, options):
            seen.append(context.snapshot)
            return ScanResult(True)

        execute(snapshot, config, definitions={
            "naming": definition(observe), "resource_location": definition(observe),
        })
        self.assertEqual(seen[0].file_paths, {"/keep.md", "/keep/a.png"})
        self.assertEqual([folder.path for folder in seen[0].folders], ["/keep/"])
        self.assertIs(seen[1], snapshot)
        self.assertEqual(len(snapshot.pages), 3)

    def test_disabled_filter_has_no_effect_even_with_prefixes_configured(self):
        snapshot = Snapshot(pages=(Page("/home.md", "home", ""),))
        callback = Mock(return_value=ScanResult(True))
        config = configuration("naming", patches={
            "naming": {"filter": {"enabled": False, "prefixes": ["/"]}},
        })
        execute(snapshot, config, definitions={"naming": definition(callback)})
        self.assertIs(callback.call_args.args[0].snapshot, snapshot)

    def test_bad_target_does_not_block_orphan_or_connectivity_scans(self):
        config = configuration("inventory", "reference_format", "reference_targets", "orphan_files", "connectivity", patches={
            "reference_format": {"depends_on": ["inventory"]},
            "reference_targets": {"depends_on": ["reference_format"]},
            "orphan_files": {"depends_on": ["reference_format"]},
            "connectivity": {"depends_on": ["reference_format"]},
        })
        snapshot = Snapshot(pages=(
            Page("/a.md", "a", "[b](/b) [missing](/missing)"),
            Page("/b.md", "b", "[a](/a)"),
        ))
        report, log, output = execute(snapshot, config)
        self.assertEqual((report.failed, report.exit_code), (1, 1))
        self.assertIn("reference_format：成功", output)
        self.assertIn("reference_targets：失败", output)
        self.assertIn("orphan_files：成功", output)
        self.assertIn("connectivity：成功", output)
        self.assertIn("/missing", log)

    def test_filtering_a_target_makes_it_missing_for_that_case(self):
        config = configuration("reference_format", "reference_targets", patches={
            "reference_targets": {
                "depends_on": ["reference_format"],
                "filter": {"enabled": True, "prefixes": ["/b.md"]},
            },
        })
        snapshot = Snapshot(pages=(Page("/a.md", "a", "[b](/b)"), Page("/b.md", "b", "")))
        report, log, output = execute(snapshot, config)
        self.assertEqual(report.failed, 1)
        self.assertEqual(output, "reference_format：成功\nreference_targets：失败\n")
        self.assertIn("/b.md", log)

    def test_detailed_text_and_timing_only_go_to_log(self):
        config = configuration("naming")
        report, log, output = execute(Snapshot(), config, definitions={
            "naming": definition(lambda context, options: ScanResult(False, "详细错误：/private/path.md:7\n第二行")),
        })
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(output, "naming：失败\n")
        self.assertIn("--- naming · 测试 scan", log)
        self.assertIn("开始时间：", log)
        self.assertIn("结束时间：", log)
        self.assertIn("耗时：", log)
        self.assertIn("详细错误：/private/path.md:7\n第二行", log)

    def test_invalid_result_is_a_runtime_failure_and_later_case_still_runs(self):
        config = configuration("inventory", "naming")
        following = Mock(return_value=ScanResult(True))
        report, log, output = execute(Snapshot(), config, definitions={
            "inventory": definition(lambda context, options: True),
            "naming": definition(following),
        })
        self.assertEqual((report.failed, report.crashed, report.exit_code), (1, 1, 2))
        following.assert_called_once()
        self.assertIn("scan 必须返回 ScanResult", log)
        self.assertEqual(output, "inventory：失败\nnaming：成功\n")

    def test_log_failure_does_not_publish_a_success_status(self):
        class UnwritableLog(StringIO):
            def write(self, text):
                raise OSError("日志设备不可写")

        output = StringIO()
        with self.assertRaisesRegex(OSError, "日志设备不可写"):
            run_scans(Snapshot(), configuration("naming"), UnwritableLog(), output, FILTER_FORBIDDEN)
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
