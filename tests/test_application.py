"""入口集成测试：真实配置和 scan，只替换数据库连接边界。"""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main
from wikijs_scan.models import Page, Snapshot


PROJECT = Path(__file__).resolve().parents[1]


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def run_application(self, snapshot=None, error=None):
        (self.directory / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        with patch("wikijs_scan.database.load_snapshot", return_value=snapshot, side_effect=error) as load:
            code = main.run_project(self.directory, self.stdout, self.stderr)
        files = list((self.directory / "logs").glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].stem.isdecimal())
        return code, files[0].read_text(encoding="utf-8"), load

    def test_full_success_and_one_database_load(self):
        snapshot = Snapshot(pages=(Page("/home.md", "home", "[other](/other)"),
                                   Page("/other.md", "other", "[home](/home)")))
        code, log, load = self.run_application(snapshot)
        self.assertEqual(code, 0)
        load.assert_called_once()
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertEqual(len(self.stdout.getvalue().splitlines()), 13)
        self.assertIn("--- reference_targets", log)
        self.assertIn("总耗时", log)
        self.assertIn("成功 12 项，失败 0 项", log)

    def test_rule_failure_does_not_stop_independent_scans(self):
        snapshot = Snapshot(pages=(Page("/home.md", "home", "[missing](/missing)"),))
        code, log, _ = self.run_application(snapshot)
        self.assertEqual(code, 1)
        self.assertIn("reference_format：成功", self.stdout.getvalue())
        self.assertIn("reference_targets：失败", self.stdout.getvalue())
        self.assertIn("connectivity：成功", self.stdout.getvalue())
        self.assertIn("resource_folders：成功", self.stdout.getvalue())
        self.assertIn("/missing.md", log)
        self.assertNotIn("/missing", self.stdout.getvalue())

    def test_invalid_config_does_not_contact_database(self):
        self.config["global"]["scan_order"] = ["misspelled"]
        code, log, load = self.run_application()
        self.assertEqual(code, 2)
        load.assert_not_called()
        self.assertIn("运行失败", log)
        self.assertIn("misspelled", self.stderr.getvalue())

    def test_connection_error_is_logged_without_password(self):
        self.config["global"]["database"]["password"] = "integration-secret"
        code, log, _ = self.run_application(error=ValueError("failure integration-secret"))
        self.assertEqual(code, 2)
        self.assertIn("failure ***", log)
        self.assertNotIn("integration-secret", log + self.stderr.getvalue())

    def test_scan_exception_is_failure_and_later_scans_continue(self):
        from wikijs_scan.registry import SCANS, ScanDefinition

        def explode(context, options):
            raise RuntimeError("scan exploded")

        replacement = ScanDefinition("异常样例", explode, lambda options: None)
        with patch.dict(SCANS, {"naming": replacement}):
            code, log, _ = self.run_application(Snapshot())
        self.assertEqual(code, 2)
        self.assertIn("scan exploded", log)
        self.assertIn("resource_folders：成功", self.stdout.getvalue())

    def test_log_directory_failure_prevents_database_access(self):
        (self.directory / "logs").write_text("occupied", encoding="utf-8")
        with patch("wikijs_scan.database.load_snapshot") as load:
            code = main.run_project(self.directory, self.stdout, self.stderr)
        self.assertEqual(code, 2)
        load.assert_not_called()
        self.assertIn("日志写入失败", self.stderr.getvalue())

    def test_cli_rejects_old_positional_directory_argument(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as raised:
            main.main(["/old/wiki/path"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
