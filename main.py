#!/usr/bin/env python3
"""扫描入口：无业务参数，读取脚本旁 config.json，运行全部已配置的 scan。

只读加载数据库一次，每项的过滤和依赖由入口控制。详细日志放在脚本旁
logs/<Unix纳秒时间戳>.txt，含起止时间、耗时、执行/跳过原因和最终结果。
退出码：0 全部成功，1 规则失败或依赖/过滤限制不满足，2 配置/I/O/运行异常。
"""

import argparse
from pathlib import Path
import sys
import time
from typing import TextIO


PROJECT_DIRECTORY = Path(__file__).resolve().parent

# 这是规则本身的限制，不是用户可关闭的配置。保留在入口以便集中审阅。
FILTER_FORBIDDEN = frozenset({
    "inventory", "resource_size", "duplicate_md5", "forbidden_patterns",
    "reference_format", "resource_folders",
})


def run_project(project_directory: Path, stdout: TextIO, stderr: TextIO) -> int:
    started = time.perf_counter()
    password = ""
    try:
        log_directory = project_directory / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        # x 模式防止极端情况下的重名覆盖；不覆盖或删除既有日志。
        log_path = log_directory / f"{time.time_ns()}.txt"
        with log_path.open("x", encoding="utf-8") as log:
            try:
                from wikijs_scan.config import load_config
                from wikijs_scan.database import load_snapshot
                from wikijs_scan.runner import run_scans, timestamp

                log.write(f"wikijs-scan · Wiki.js 3.0.2\n开始时间：{timestamp()}\n")
                configuration = load_config(project_directory / "config.json")
                password = configuration.database["password"]
                snapshot = load_snapshot(configuration.database)
                log.write(f"读取数据：页面 {len(snapshot.pages)}，资源 {len(snapshot.assets)}，目录 {len(snapshot.folders)}\n")
                report = run_scans(snapshot, configuration, log, stdout, FILTER_FORBIDDEN)
                total_status = "成功" if report.exit_code == 0 else "失败"
                log.write(f"\n--- 总结果\n结束时间：{timestamp()}\n总耗时：{time.perf_counter() - started:.6f}s\n")
                log.write(f"结果：{total_status}\n共 {report.total} 项，成功 {report.total - report.failed} 项，失败 {report.failed} 项\n")
                log.flush()
                print(f"总结果：{total_status}", file=stdout, flush=True)
                return report.exit_code
            except Exception as exc:
                # 连接错误可能带 DSN，不写配置或未脱敏的连接异常。
                detail = str(exc).replace(password, "***") if password else str(exc)
                if isinstance(exc, ModuleNotFoundError):
                    detail += "；请先运行 python -m pip install -r requirements.txt"
                log.write(f"\n--- 运行失败\n总耗时：{time.perf_counter() - started:.6f}s\n{detail}\n")
                log.flush()
                print("总结果：失败", file=stdout, flush=True)
                print(f"无法完成扫描：{detail}（日志：{log_path}）", file=stderr)
                return 2
    except OSError as exc:
        print("总结果：失败", file=stdout, flush=True)
        print(f"日志写入失败：{exc}", file=stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 config.json 扫描 Wiki.js 3.0.2 数据库")
    parser.parse_args(argv)
    return run_project(PROJECT_DIRECTORY, sys.stdout, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
