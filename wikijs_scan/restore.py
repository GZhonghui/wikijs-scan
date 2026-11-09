"""把停库生成的 PostgreSQL 18 整卷 tar.gz 恢复为独立 Docker 数据库。

流程：校验配置与整个归档 -> 拒绝容器名冲突、被占用或非空的目标卷 ->
准备相同架构的 PostgreSQL 镜像 -> 向空卷恢复目录、权限和数字 UID/GID ->
启动数据库并验证配置中的原有凭据。不会启动 Wiki.js，不清空已有卷、不改密码，
失败时保留已经恢复的数据供排查；本模块只有独立恢复入口才会调用。

归档格式对应用户的 backup_wikijs_volume.sh：卷根下 18/docker/PG_VERSION。
为防止解压逃逸，仅接收普通文件/目录；外部 tablespace 等链接式布局明确不支持。
POSTGRES_* 环境变量仅初始化空库，对恢复的数据库无效，所以这里不使用它们。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
from typing import Any, BinaryIO


class RestoreError(ValueError):
    """恢复前提不满足或恢复过程失败；原有容器和数据不被删除。"""


RESTORE_DEFAULTS = {
    "image": "postgres:18.6-trixie",
    "platform": "linux/amd64",
    "volume_name": "wiki-prod_db-data",
    "container_name": "wikijs-scan-db",
    "bind_host": "127.0.0.1",
    "host_port": 55432,
    "ready_timeout_seconds": 60,
}


@dataclass(frozen=True)
class ArchiveInfo:
    members: int
    file_bytes: int


def validate_restore_config(options: Mapping[str, Any], database: Mapping[str, Any]) -> dict[str, Any]:
    """返回规范配置；端口与扫描连接保持一致，数据库仍使用备份中的凭据。"""
    if not isinstance(options, Mapping):
        raise ValueError("global.restore 必须是对象")
    unknown = set(options) - RESTORE_DEFAULTS.keys()
    if unknown:
        raise ValueError(f"global.restore 包含未知配置：{', '.join(sorted(unknown))}")
    config = {**RESTORE_DEFAULTS, **options}
    for key in ("volume_name", "container_name"):
        value = config[key]
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
            raise ValueError(f"global.restore.{key} 不是有效的 Docker 名称")
    image = config["image"]
    if not isinstance(image, str) or not image or image.startswith("-") or any(c.isspace() for c in image):
        raise ValueError("global.restore.image 必须是有效的非空镜像名")
    if config["platform"] != "linux/amd64":
        raise ValueError("物理备份须与源数据库架构一致：platform 必须为 linux/amd64")
    try:
        ipaddress.ip_address(config["bind_host"])
    except (TypeError, ValueError) as exc:
        raise ValueError("global.restore.bind_host 必须是 IP 地址") from exc
    for key, maximum in (("host_port", 65535), ("ready_timeout_seconds", 86400)):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"global.restore.{key} 必须是 1 到 {maximum} 的整数")
    if config["host_port"] != database.get("port"):
        raise ValueError("global.restore.host_port 必须与 global.database.port 一致")
    return config


def _member_path(member: tarfile.TarInfo) -> str:
    name = member.name
    if (not name or "\x00" in name or "\\" in name or name.startswith("/")
            or ".." in name.split("/")):
        raise RestoreError(f"归档含不安全路径：{name!r}")
    path = str(PurePosixPath(name))
    if not (member.isdir() or member.isfile()) or member.issparse():
        raise RestoreError(f"归档只允许普通文件和目录，不允许链接/设备/稀疏文件：{name!r}")
    if path == "." and not member.isdir():
        raise RestoreError("归档根成员必须是目录")
    # 官方镜像的卷根可能带 sticky/setgid；目录的这些位需要原样保留。
    # 普通数据文件不应携带提权位，目录 setuid 也不属于支持的布局。
    invalid_mode = member.mode & (0o4000 if member.isdir() else 0o7000)
    if member.uid < 0 or member.gid < 0 or invalid_mode:
        raise RestoreError(f"归档成员包含不支持的所有权或特殊权限：{name!r}")
    return path


def validate_archive(path: Path) -> ArchiveInfo:
    """验证归档内容而不在宿主机解压；检查 gzip 校验和及 PG18 数据目录层级。"""
    seen: dict[str, bool] = {}
    file_bytes = 0
    version = None
    has_control = False
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                name = _member_path(member)
                if name in seen:
                    raise RestoreError(f"归档含重复路径：{name}")
                seen[name] = member.isdir()
                for parent in PurePosixPath(name).parents:
                    if seen.get(str(parent)) is False:
                        raise RestoreError(f"归档父路径不是目录：{parent}")
                if member.isfile():
                    file_bytes += member.size
                    if name == "18/docker/PG_VERSION":
                        if member.size > 16:
                            raise RestoreError("PG_VERSION 内容无效")
                        stream = archive.extractfile(member)
                        assert stream is not None
                        version = stream.read().strip()
                    if name == "18/docker/global/pg_control":
                        has_control = member.size > 0
            # tar 的结束标记可能早于 gzip 结束；继续读取以验证尾部与 CRC。
            while archive.fileobj.read(1024 * 1024):
                pass
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise RestoreError(f"无法读取完整 tar.gz 备份：{exc}") from exc
    if version != b"18" or not has_control:
        raise RestoreError("备份必须包含 18/docker/PG_VERSION=18 和 global/pg_control，请提供 PG18 整卷备份")
    # 即使父文件条目写在子条目之后，也拒绝这种路径冲突。
    for name in seen:
        if any(seen.get(str(parent)) is False for parent in PurePosixPath(name).parents):
            raise RestoreError(f"归档含文件/目录路径冲突：{name}")
    return ArchiveInfo(len(seen), file_bytes)


def _docker(args: list[str], *, stdin: BinaryIO | None = None,
            timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """仅使用参数数组调用 Docker，配置和归档名称不会被解释为 shell 代码。"""
    try:
        result = subprocess.run(["docker", *args], stdin=stdin, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError as exc:
        raise RestoreError("找不到 docker 命令") from exc
    except subprocess.TimeoutExpired as exc:
        raise RestoreError(f"Docker 操作超时：{args[0]}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RestoreError(f"Docker {args[0]} 失败：{detail}")
    return result


def _inspect(kind: str, name: str) -> dict[str, Any] | None:
    result = _docker([kind, "inspect", name], check=False)
    if result.returncode:
        # 不把权限不足、daemon 失联等错误误判为资源不存在。
        if "no such" in result.stderr.lower():
            return None
        raise RestoreError(f"无法检查 Docker {kind}：{result.stderr.strip()}")
    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("expected one object")
        return rows[0]
    except (ValueError, TypeError, IndexError) as exc:
        raise RestoreError(f"Docker {kind} inspect 返回了无效数据") from exc


def _ensure_image(config: Mapping[str, Any]) -> None:
    image = _inspect("image", config["image"])
    if image is None:
        _docker(["pull", "--platform", config["platform"], config["image"]])
        image = _inspect("image", config["image"])
    if not image or image.get("Architecture") != "amd64" or image.get("Os") != "linux":
        raise RestoreError("恢复镜像必须是 linux/amd64，与源数据库一致")
    version = _docker(["run", "--rm", "--platform", config["platform"],
                       "--entrypoint", "postgres", config["image"], "--version"]).stdout
    if not re.search(r"\bPostgreSQL\)?\s+18(?:\.|\s|$)", version):
        raise RestoreError("恢复镜像必须提供 PostgreSQL 18")


def _ensure_empty_volume(config: Mapping[str, Any]) -> None:
    name = config["volume_name"]
    attached = _docker(["ps", "--all", "--quiet", "--filter", f"volume={name}"]).stdout.strip()
    if attached:
        raise RestoreError(f"目标卷已被容器使用，拒绝恢复：{name}")
    volume = _inspect("volume", name)
    if volume is None:
        _docker(["volume", "create", "--label", "wikijs-scan.restore=true", name])
        volume = _inspect("volume", name)
    if not volume or volume.get("Driver") != "local" or volume.get("Options"):
        raise RestoreError("恢复仅支持普通 local Docker 卷，不接受绑定宿主机目录的卷")
    result = _docker(["run", "--rm", "--platform", config["platform"],
                      "--mount", f"type=volume,src={name},dst=/restore,readonly",
                      "--entrypoint", "find", config["image"],
                      "/restore", "-mindepth", "1", "-print", "-quit"])
    if result.stdout.strip():
        raise RestoreError(f"目标卷不是空卷，拒绝覆盖：{name}")


def _verify_connection(database: Mapping[str, Any]) -> None:
    import psycopg

    keys = ("host", "port", "dbname", "user", "password", "sslmode")
    kwargs = {key: database[key] for key in keys if key in database}
    with psycopg.connect(**kwargs, connect_timeout=min(database.get("connect_timeout", 5), 5)) as connection:
        connection.read_only = True
        connection.execute("SELECT 1")


def _wait_for_database(config: Mapping[str, Any], database: Mapping[str, Any]) -> None:
    deadline = time.monotonic() + config["ready_timeout_seconds"]
    while time.monotonic() < deadline:
        result = _docker(["exec", config["container_name"], "pg_isready", "--quiet",
                          "--username", database["user"], "--dbname", database["dbname"]],
                         timeout=5, check=False)
        if result.returncode == 0:
            try:
                _verify_connection(database)
                return
            except Exception as exc:
                # 凭据不匹配不靠等待解决；错误信息也不回显配置中的密码。
                message = str(exc)
                password = database.get("password")
                if isinstance(password, str) and password:
                    message = message.replace(password, "***")
                raise RestoreError("数据库已启动，但配置中的地址或原有凭据连接失败：" + message) from None
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise RestoreError("数据库未在限定时间内就绪；已保留容器与数据卷，可查看 docker logs 排查")


def restore_database(archive_path: Path, options: Mapping[str, Any], database: Mapping[str, Any],
                     progress: Callable[[str], None] | None = None) -> str:
    """显式执行恢复；成功返回摘要，失败抛 RestoreError，不删除已有资源。"""
    report = progress or (lambda message: None)
    config = validate_restore_config(options, database)
    path = archive_path.expanduser().resolve(strict=True)
    if not path.is_file():
        raise RestoreError("备份路径必须是普通 tar.gz 文件")
    report("正在校验备份归档……")
    info = validate_archive(path)
    _docker(["info", "--format", "{{.ServerVersion}}"], timeout=15)
    if _inspect("container", config["container_name"]) is not None:
        raise RestoreError(f"目标容器已存在，拒绝覆盖：{config['container_name']}")
    report("正在检查镜像与空数据卷……")
    _ensure_image(config)
    _ensure_empty_volume(config)
    report(f"正在恢复 {info.members} 个归档成员，共 {info.file_bytes} 字节……")
    # 固定 shell 片段只用于在同一个容器内再次检查空卷；用户输入不进入脚本正文。
    extract_script = (
        'if [ -n "$(find /restore -mindepth 1 -print -quit)" ]; then '
        'echo "restore volume is not empty" >&2; exit 2; fi; '
        'exec tar --numeric-owner --same-owner --same-permissions -xzpf - -C /restore'
    )
    with path.open("rb") as source:
        # 固定于卷名的 helper 名称也防止两份恢复程序同时写同一个卷。
        _docker(["run", "--rm", "--interactive",
                 "--name", f"wikijs-scan-restore-{config['volume_name']}",
                 "--platform", config["platform"],
                 "--mount", f"type=volume,src={config['volume_name']},dst=/restore",
                 "--entrypoint", "sh", config["image"], "-ec", extract_script], stdin=source)
    bind = config["bind_host"]
    if ":" in bind:
        bind = f"[{bind}]"
    _docker(["run", "--detach", "--name", config["container_name"],
             "--label", "wikijs-scan.restore=true", "--platform", config["platform"],
             "--publish", f"{bind}:{config['host_port']}:5432",
             "--mount", f"type=volume,src={config['volume_name']},dst=/var/lib/postgresql",
             config["image"]])
    report("正在等待数据库就绪并验证原有凭据……")
    _wait_for_database(config, database)
    return (f"恢复成功：容器 {config['container_name']}，卷 {config['volume_name']}，"
            f"端口 {config['bind_host']}:{config['host_port']}；可以运行 main.py。")
