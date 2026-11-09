"""按配置顺序调度 scan；依赖只决定当前项是否执行，不改变顺序。

每项失败或抛出异常后仍处理下一项。前置未成功、禁止过滤却打开过滤时，
本项不调用 run，但仍写入一个失败结果。详细文本、执行原因与耗时统一写日志；
控制台仅输出 scan ID 和成功/失败。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import time
import traceback
from typing import Mapping, TextIO

from .config import Configuration
from .filtering import filter_snapshot
from .models import ScanContext, ScanResult, Snapshot
from .registry import SCANS, ScanDefinition


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class RunReport:
    total: int
    failed: int
    crashed: int

    @property
    def exit_code(self) -> int:
        return 2 if self.crashed else 1 if self.failed else 0


def run_scans(
    snapshot: Snapshot,
    configuration: Configuration,
    log: TextIO,
    stdout: TextIO,
    forbidden_filter: frozenset[str],
    definitions: Mapping[str, ScanDefinition] = SCANS,
) -> RunReport:
    outcomes: dict[str, bool] = {}
    failures = crashes = 0
    for spec in configuration.scans:
        definition = definitions[spec.scan_id]
        started_at, start = timestamp(), time.perf_counter()
        execution = "已执行"
        dependencies = [dependency for dependency in spec.depends_on if not outcomes.get(dependency, False)]
        try:
            if spec.filter.enabled and spec.scan_id in forbidden_filter:
                execution = "未执行：本 scan 禁止过滤"
                result = ScanResult(False, "配置错误：本 scan 禁用白名单，请将 filter.enabled 设为 false。")
            elif dependencies:
                execution = "未执行：依赖未满足"
                reasons = [f"{dependency}（{'失败' if dependency in outcomes else '尚未执行或未启用'}）"
                           for dependency in dependencies]
                result = ScanResult(False, "依赖未成功执行：" + "、".join(reasons))
            else:
                view = filter_snapshot(snapshot, spec.filter.prefixes) if spec.filter.enabled else snapshot
                result = definition.run(ScanContext(view, configuration.settings), spec.options)
                if not isinstance(result, ScanResult) or type(result.success) is not bool or not isinstance(result.log_text, str):
                    raise TypeError("scan 必须返回 ScanResult(success: bool, log_text: str)")
        except Exception:
            crashes += 1
            execution = "运行异常"
            result = ScanResult(False, traceback.format_exc().rstrip())

        elapsed = time.perf_counter() - start
        outcomes[spec.scan_id] = result.success
        failures += not result.success
        status = "成功" if result.success else "失败"
        # 先持久化结果再公布状态；日志不可写时不宣称已完成扫描。
        log.write(f"\n--- {spec.scan_id} · {definition.title}\n")
        log.write(f"开始时间：{started_at}\n结束时间：{timestamp()}\n耗时：{elapsed:.6f}s\n")
        log.write(f"结果：{status}\n执行状态：{execution}\n")
        if spec.filter.enabled:
            log.write(f"忽略前缀：{list(spec.filter.prefixes)!r}\n")
        if result.log_text:
            log.write(result.log_text.rstrip() + "\n")
        log.flush()
        print(f"{spec.scan_id}：{status}", file=stdout, flush=True)
    return RunReport(len(configuration.scans), failures, crashes)
