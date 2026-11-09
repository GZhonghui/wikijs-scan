#!/usr/bin/env python3
"""恢复停库备份到独立数据库：python restore_database.py /path/to/backup.tar.gz。

所有连接、卷名、容器名和镜像参数均从本脚本同目录的 config.json 读取。
这是显式恢复入口；main.py 只读扫描时不会调用它。恢复不覆盖现有数据。
"""

import argparse
from pathlib import Path
import sys


PROJECT_DIRECTORY = Path(__file__).resolve().parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="将 PostgreSQL 18 整卷 tar.gz 恢复为独立扫描数据库")
    parser.add_argument("archive", type=Path, help="停库生成的数据库卷 tar.gz 备份")
    args = parser.parse_args(argv)
    try:
        from wikijs_scan.config import load_config
        from wikijs_scan.restore import restore_database

        config = load_config(PROJECT_DIRECTORY / "config.json")
        summary = restore_database(args.archive, config.restore, config.database, progress=print)
    except (ValueError, OSError, ModuleNotFoundError) as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("恢复已中断；已创建的容器和数据卷会保留，请检查后再操作。", file=sys.stderr)
        return 130
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
