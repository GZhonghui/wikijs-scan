"""配置行为测试：规则选择、依赖声明、类型校验及 JSON 重复键。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from main import FILTER_FORBIDDEN
from wikijs_scan.config import load_config, parse_config


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]


def minimal_config(*scan_ids):
    return {
        "global": {"database": {}, "scan_order": list(scan_ids)},
        "scans": {scan_id: {} for scan_id in scan_ids},
    }


class ConfigurationTests(unittest.TestCase):
    def test_shipped_configuration_selects_all_twelve_cases_and_expected_dependencies(self):
        config = load_config(PROJECT_DIRECTORY / "config.json")
        specs = {spec.scan_id: spec for spec in config.scans}
        self.assertEqual(set(specs), {
            "inventory", "resource_location", "resource_size", "duplicate_sizes",
            "naming", "forbidden_patterns", "reference_format", "reference_targets",
            "orphan_files", "connectivity", "folder_index", "resource_folders",
        })
        self.assertEqual(specs["reference_format"].depends_on, ("inventory",))
        for scan_id in ("reference_targets", "orphan_files", "connectivity"):
            self.assertEqual(specs[scan_id].depends_on, ("reference_format",))
        self.assertTrue(all(not spec.filter.enabled for spec in config.scans))
        self.assertEqual(FILTER_FORBIDDEN, {
            "inventory", "resource_size", "duplicate_sizes", "forbidden_patterns",
            "reference_format", "resource_folders",
        })

    def test_execution_order_is_preserved_even_when_dependencies_are_later_or_cyclic(self):
        raw = minimal_config("resource_size", "inventory")
        raw["scans"]["resource_size"]["depends_on"] = ["inventory"]
        raw["scans"]["inventory"]["depends_on"] = ["resource_size"]
        config = parse_config(raw)
        self.assertEqual([spec.scan_id for spec in config.scans], ["resource_size", "inventory"])
        self.assertEqual(config.scans[0].depends_on, ("inventory",))
        self.assertEqual(config.scans[1].depends_on, ("resource_size",))

    def test_disabled_and_unordered_cases_do_not_execute_but_dependencies_are_retained(self):
        raw = minimal_config("inventory", "reference_format")
        raw["scans"]["inventory"]["enabled"] = False
        raw["scans"]["reference_format"]["depends_on"] = ["inventory"]
        raw["scans"]["resource_size"] = {"enabled": True}
        config = parse_config(raw)
        self.assertEqual([spec.scan_id for spec in config.scans], ["reference_format"])
        self.assertEqual(config.scans[0].depends_on, ("inventory",))

    def test_registered_dependency_may_be_absent_and_is_left_for_runtime(self):
        raw = minimal_config("reference_targets")
        raw["scans"]["reference_targets"]["depends_on"] = ["reference_format"]
        self.assertEqual(parse_config(raw).scans[0].depends_on, ("reference_format",))

    def test_filter_restriction_is_not_a_global_configuration_failure(self):
        raw = minimal_config("inventory")
        raw["scans"]["inventory"]["filter"] = {"enabled": True, "prefixes": ["/home"]}
        config = parse_config(raw)
        self.assertTrue(config.scans[0].filter.enabled)
        self.assertEqual(config.scans[0].filter.prefixes, ("/home",))

    def test_prefixes_are_literal_and_are_not_rewritten_as_globs(self):
        raw = minimal_config("naming")
        raw["scans"]["naming"]["filter"] = {
            "enabled": True, "prefixes": ["/home", "/assets/", "/literal*"],
        }
        config = parse_config(raw)
        self.assertEqual(config.scans[0].filter.prefixes, ("/home", "/assets/", "/literal*"))

    def test_unknown_fields_are_rejected_at_all_configuration_levels(self):
        paths = [
            (), ("global",), ("global", "database"), ("global", "restore"),
            ("scans",), ("scans", "inventory"), ("scans", "inventory", "filter"),
            ("scans", "inventory", "options"),
        ]
        for path in paths:
            with self.subTest(path=path):
                raw = minimal_config("inventory")
                target = raw
                for key in path:
                    target = target.setdefault(key, {})
                target["typo"] = True
                with self.assertRaises(ValueError):
                    parse_config(raw)

    def test_wrong_json_types_and_invalid_values_are_rejected(self):
        cases = [
            (("global", "scan_order"), "inventory"),
            (("global", "scan_order"), ["inventory", "inventory"]),
            (("global", "scan_order"), ["not_registered"]),
            (("global", "database", "port"), True),
            (("global", "database", "port"), 65536),
            (("global", "database", "password"), None),
            (("global", "database", "sslmode"), "invalid"),
            (("global", "restore", "host_port"), "55432"),
            (("global", "IMAGES_DIR"), "images/"),
            (("global", "DOCS_DIR"), "images"),
            (("global", "external_prefixes"), [None]),
            (("scans", "inventory", "enabled"), "false"),
            (("scans", "inventory", "depends_on"), "naming"),
            (("scans", "inventory", "depends_on"), ["naming", "naming"]),
            (("scans", "inventory", "depends_on"), ["not_registered"]),
            (("scans", "inventory", "filter", "enabled"), 1),
            (("scans", "inventory", "filter", "prefixes"), ["relative"]),
            (("scans", "inventory", "options"), []),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=value):
                raw = minimal_config("inventory")
                target = raw
                for key in path[:-1]:
                    target = target.setdefault(key, {})
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    parse_config(raw)

    def test_missing_configurations_and_no_enabled_case_are_errors(self):
        for raw in (None, {}, {"global": {}}, {"scans": {}}, minimal_config()):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_config(raw)
        raw = minimal_config("inventory")
        del raw["scans"]["inventory"]
        with self.assertRaises(ValueError):
            parse_config(raw)
        raw = minimal_config("inventory")
        raw["scans"]["inventory"]["enabled"] = False
        with self.assertRaises(ValueError):
            parse_config(raw)

    def test_invalid_options_are_rejected_even_for_disabled_or_unordered_cases(self):
        for enabled in (False, True):
            raw = minimal_config("inventory")
            raw["scans"]["resource_size"] = {"enabled": enabled, "options": {"max_bytes": True}}
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                parse_config(raw)

    def test_duplicate_json_keys_cannot_silently_override_configuration(self):
        nested = json.dumps(minimal_config("inventory")).replace(
            '"inventory": {}', '"inventory": {"enabled": true, "enabled": false}',
        )
        documents = ['{"global": {}, "global": {}, "scans": {}}', nested]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for document in documents:
                with self.subTest(document=document):
                    path.write_text(document, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "重复字段"):
                        load_config(path)

    def test_bom_file_is_supported_and_parsing_does_not_mutate_input(self):
        raw = minimal_config("resource_size")
        before = deepcopy(raw)
        config = parse_config(raw)
        self.assertEqual(raw, before)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8-sig")
            self.assertEqual(load_config(path), config)


if __name__ == "__main__":
    unittest.main()
