"""集中注册 scan 的稳定 ID 与接口；新增规则不需要修改调度算法。"""

from dataclasses import dataclass
from typing import Callable

from .models import ScanContext, ScanResult
from .scans import (
    connectivity, duplicate_sizes, folder_index, forbidden_patterns, inventory,
    naming, orphan_files, reference_format, reference_targets, resource_folders,
    resource_location, resource_size,
)


@dataclass(frozen=True)
class ScanDefinition:
    title: str
    run: Callable[[ScanContext, dict], ScanResult]
    validate_options: Callable[[dict], None]


SCANS = {
    "inventory": ScanDefinition("目录与文件统计", inventory.run, inventory.validate_options),
    "resource_location": ScanDefinition("资源文件位置", resource_location.run, resource_location.validate_options),
    "resource_size": ScanDefinition("资源文件大小", resource_size.run, resource_size.validate_options),
    "duplicate_sizes": ScanDefinition("资源大小重复", duplicate_sizes.run, duplicate_sizes.validate_options),
    "naming": ScanDefinition("标题与路径命名", naming.run, naming.validate_options),
    "forbidden_patterns": ScanDefinition("禁止词语与格式", forbidden_patterns.run, forbidden_patterns.validate_options),
    "reference_format": ScanDefinition("引用格式与自引用", reference_format.run, reference_format.validate_options),
    "reference_targets": ScanDefinition("引用目标存在性", reference_targets.run, reference_targets.validate_options),
    "orphan_files": ScanDefinition("零入链文件", orphan_files.run, orphan_files.validate_options),
    "connectivity": ScanDefinition("文件弱连通性", connectivity.run, connectivity.validate_options),
    "folder_index": ScanDefinition("文件夹对应页面", folder_index.run, folder_index.validate_options),
    "resource_folders": ScanDefinition("资源目录嵌套", resource_folders.run, resource_folders.validate_options),
}
