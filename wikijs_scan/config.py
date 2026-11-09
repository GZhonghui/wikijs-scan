"""读取唯一的 JSON 配置，拒绝拼写错误、重复键和不正确的类型。

配置结构是 global + scans。依赖是用户配置的执行条件，不进行拓扑排序或
依赖环检查：没有先成功运行的前置项，由调度器在运行当前项时判为失败。
禁用过滤的规则也在运行阶段判断，使单项配置问题不会阻断其他 scan。
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .models import GlobalSettings
from .registry import SCANS
from .restore import RESTORE_DEFAULTS


@dataclass(frozen=True)
class FilterSpec:
    enabled: bool = False
    prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanSpec:
    scan_id: str
    depends_on: tuple[str, ...]
    filter: FilterSpec
    options: dict


@dataclass(frozen=True)
class Configuration:
    settings: GlobalSettings
    database: dict
    restore: dict
    scans: tuple[ScanSpec, ...]


def _object(value: object, where: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{where} 包含未知字段：{', '.join(sorted(unknown))}")
    return value


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where} 必须是布尔值")
    return value


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{where} 必须是非空字符串组成的数组")
    return tuple(value)


def _unique(value: tuple[str, ...], where: str) -> None:
    if len(value) != len(set(value)):
        raise ValueError(f"{where} 不允许重复项")


def _database(value: object) -> dict:
    defaults = {
        "host": "127.0.0.1", "port": 55432, "dbname": "wiki", "user": "wikijs",
        "password": "", "connect_timeout": 10, "statement_timeout_ms": 30000,
        "sslmode": "prefer", "schema": "wiki",
    }
    data = defaults | _object(value, "global.database", set(defaults))
    for key in ("host", "dbname", "user", "sslmode", "schema"):
        if not isinstance(data[key], str) or not data[key] or "\0" in data[key]:
            raise ValueError(f"global.database.{key} 必须是非空字符串")
    if not isinstance(data["password"], str) or "\0" in data["password"]:
        raise ValueError("global.database.password 必须是字符串且不含空字符")
    for key in ("port", "connect_timeout", "statement_timeout_ms"):
        if type(data[key]) is not int or data[key] <= 0:
            raise ValueError(f"global.database.{key} 必须是正整数")
    if data["port"] > 65535:
        raise ValueError("global.database.port 超出端口范围")
    if data["sslmode"] not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
        raise ValueError("global.database.sslmode 不受支持")
    return data


def _restore(value: object) -> dict:
    defaults = RESTORE_DEFAULTS
    data = defaults | _object(value, "global.restore", set(defaults))
    for key in ("image", "platform", "volume_name", "container_name", "bind_host"):
        if not isinstance(data[key], str) or not data[key] or "\0" in data[key]:
            raise ValueError(f"global.restore.{key} 必须是非空字符串")
    for key in ("host_port", "ready_timeout_seconds"):
        if type(data[key]) is not int or data[key] <= 0:
            raise ValueError(f"global.restore.{key} 必须是正整数")
    if data["host_port"] > 65535:
        raise ValueError("global.restore.host_port 超出端口范围")
    return data


def parse_config(value: object) -> Configuration:
    root = _object(value, "配置", {"global", "scans"})
    global_config = _object(root.get("global"), "global", {
        "database", "restore", "scan_order", "IMAGES_DIR", "DOCS_DIR", "external_prefixes",
    })
    scans = _object(root.get("scans"), "scans", set(SCANS))
    order = _strings(global_config.get("scan_order"), "global.scan_order")
    _unique(order, "global.scan_order")
    for scan_id in order:
        if scan_id not in SCANS or scan_id not in scans:
            raise ValueError(f"scan_order 中的 {scan_id!r} 未注册或缺少配置")

    images_dir = global_config.get("IMAGES_DIR", "images")
    docs_dir = global_config.get("DOCS_DIR", "docs")
    for name, directory in (("IMAGES_DIR", images_dir), ("DOCS_DIR", docs_dir)):
        if not isinstance(directory, str) or not re.fullmatch(r"[a-z0-9-]+", directory):
            raise ValueError(f"global.{name} 必须是只含小写字母、数字或连字符的单级目录名")
    if images_dir == docs_dir:
        raise ValueError("IMAGES_DIR 与 DOCS_DIR 不能相同")
    prefixes = _strings(global_config.get("external_prefixes", ["http", "mailto"]), "global.external_prefixes")
    settings = GlobalSettings(images_dir, docs_dir, prefixes)

    specifications = {}
    for scan_id, raw in scans.items():
        item = _object(raw, f"scans.{scan_id}", {"enabled", "depends_on", "filter", "options"})
        enabled = _boolean(item.get("enabled", True), f"scans.{scan_id}.enabled")
        dependencies = _strings(item.get("depends_on", []), f"scans.{scan_id}.depends_on")
        _unique(dependencies, f"scans.{scan_id}.depends_on")
        for dependency in dependencies:
            if dependency not in SCANS:
                raise ValueError(f"{scan_id} 依赖未注册的 scan：{dependency}")
        filtering = _object(item.get("filter", {}), f"scans.{scan_id}.filter", {"enabled", "prefixes"})
        filter_enabled = _boolean(filtering.get("enabled", False), f"scans.{scan_id}.filter.enabled")
        ignored = _strings(filtering.get("prefixes", []), f"scans.{scan_id}.filter.prefixes")
        if any(not path.startswith("/") or "\0" in path for path in ignored):
            raise ValueError(f"scans.{scan_id}.filter.prefixes 中的路径必须以 / 开头且不含空字符")
        options = item.get("options", {})
        SCANS[scan_id].validate_options(options)
        if enabled:
            specifications[scan_id] = ScanSpec(scan_id, dependencies, FilterSpec(filter_enabled, ignored), options)
    selected = tuple(specifications[scan_id] for scan_id in order if scan_id in specifications)
    if not selected:
        raise ValueError("scan_order 中必须至少有一个启用的 scan")
    return Configuration(
        settings=settings,
        database=_database(global_config.get("database")),
        restore=_restore(global_config.get("restore", {})),
        scans=selected,
    )


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 存在重复字段：{key}")
        result[key] = value
    return result


def load_config(path: Path) -> Configuration:
    with path.open(encoding="utf-8-sig") as source:
        value = json.load(source, object_pairs_hook=_no_duplicate_keys)
    return parse_config(value)
