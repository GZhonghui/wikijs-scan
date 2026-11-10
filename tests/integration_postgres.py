"""显式运行的 Docker/PG18 集成测试，不属于默认 unittest discover。

python tests/integration_postgres.py

只创建随机命名且带 wikijs-scan.integration 标签的测试容器和卷。用最小真实
PostgreSQL schema 验证 SQL、媒体列权限和完整停库备份恢复；最后清理本次
创建的资源。不会读取 config.json 中的连接信息，不接触用户的数据库。
"""

from contextlib import closing
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql

from main import FILTER_FORBIDDEN
from wikijs_scan.config import load_config
from wikijs_scan.database import DatabaseError, load_snapshot
from wikijs_scan.models import ScanContext
from wikijs_scan.restore import restore_database
from wikijs_scan.runner import run_scans
from wikijs_scan.scans import duplicate_md5


IMAGE = "postgres:18.6-trixie"
PROJECT = Path(__file__).resolve().parents[1]


def docker(*arguments, **kwargs):
    return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def free_port():
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class PostgreSQLIntegration(unittest.TestCase):
    def test_readonly_snapshot_and_volume_round_trip(self):
        run_id = "wikijs-scan-test-" + uuid.uuid4().hex[:12]
        source, target = run_id + "-source", run_id + "-restored"
        source_volume, target_volume = source + "-data", target + "-data"
        administrator_password, reader_password = uuid.uuid4().hex, uuid.uuid4().hex
        port = free_port()
        docker("info", "--format", "{{.ServerVersion}}")
        available = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
        if available.returncode:
            print(f"下载集成测试镜像 {IMAGE}", flush=True)
            docker("pull", "--platform", "linux/amd64", IMAGE)
        # 名字由本次 UUID 唯一生成；清理范围仅限这四个测试资源。
        try:
            docker("volume", "create", "--label", "wikijs-scan.integration=true", source_volume)
            environment = dict(os.environ, POSTGRES_PASSWORD=administrator_password)
            docker("run", "--detach", "--name", source, "--label", "wikijs-scan.integration=true",
                   "--platform", "linux/amd64", "-e", "POSTGRES_USER=wikijs_test", "-e", "POSTGRES_DB=wiki_test",
                   "-e", "POSTGRES_PASSWORD", "-p", f"127.0.0.1:{port}:5432",
                   "--mount", f"type=volume,src={source_volume},dst=/var/lib/postgresql", IMAGE, env=environment)
            admin = {"host": "127.0.0.1", "port": port, "dbname": "wiki_test",
                     "user": "wikijs_test", "password": administrator_password, "connect_timeout": 2}
            deadline = time.monotonic() + 60
            while True:
                try:
                    connection = psycopg.connect(**admin)
                    break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            with connection:
                connection.execute('CREATE EXTENSION ltree')
                connection.execute('CREATE SCHEMA wiki')
                connection.execute('SET LOCAL search_path TO wiki, public')
                connection.execute('''
                    CREATE TABLE sites (id uuid PRIMARY KEY);
                    CREATE TABLE pages (id uuid PRIMARY KEY, path text, title text, content text,
                        "contentType" text, locale text, "siteId" uuid REFERENCES sites);
                    CREATE TYPE "treeType" AS ENUM ('folder', 'page', 'asset');
                    CREATE TABLE tree (id uuid PRIMARY KEY, "folderPath" ltree, "fileName" text,
                        tree "treeType", locale text, title text, "siteId" uuid REFERENCES sites);
                    CREATE TABLE assets (id uuid PRIMARY KEY, "fileName" text, "fileSize" bigint,
                        data bytea, preview bytea, "siteId" uuid REFERENCES sites);
                ''')
                site_id = uuid.uuid4()
                connection.execute("INSERT INTO sites VALUES (%s)", (site_id,))
                pages = {
                    "home": "[guides](/guides)\n![](/_files/guides/images/a.png)\n![](/_files/guides/images/b.png)",
                    "guides": "[home](/home)\n[intro](/guides/intro)",
                    "guides/intro": "[guides](/guides)",
                }
                for path, content in pages.items():
                    record_id = uuid.uuid4()
                    name = path.rsplit("/", 1)[-1]
                    connection.execute('INSERT INTO pages VALUES (%s,%s,%s,%s,%s,%s,%s)',
                                       (record_id, path, name, content, "markdown", "en", site_id))
                    self.insert_tree(connection, site_id, record_id, path, "page")
                for path in ("guides", "guides/images", "empty"):
                    self.insert_tree(connection, site_id, uuid.uuid4(), path, "folder")
                contents = {"a.png": b"\x00\xff\x80a", "b.png": b"\x00\xff\x80b"}
                for name, content in contents.items():
                    asset_id = uuid.uuid4()
                    connection.execute('INSERT INTO assets VALUES (%s,%s,%s,%s,%s,%s)',
                                       (asset_id, name, len(content), content, b"never selected", site_id))
                    self.insert_tree(connection, site_id, asset_id, "guides/images/" + name, "asset")
                connection.execute(sql.SQL("CREATE ROLE scan_reader LOGIN PASSWORD {}").format(sql.Literal(reader_password)))
                connection.execute("GRANT USAGE ON SCHEMA wiki TO scan_reader")
                connection.execute("GRANT SELECT ON sites, pages, tree TO scan_reader")
                # 允许读取原始内容；若代码用 SELECT * 或读取 preview，真实查询仍会失败。
                connection.execute('GRANT SELECT (id,"fileName","fileSize","siteId",data) ON assets TO scan_reader')
            database = dict(admin, user="scan_reader", password=reader_password,
                            sslmode="prefer", statement_timeout_ms=5000, schema="wiki")
            first = load_snapshot(database)
            self.assertEqual(load_snapshot({key: value for key, value in database.items() if key != "schema"}), first)
            with self.assertRaisesRegex(DatabaseError, r"global\.database\.schema"):
                load_snapshot(dict(database, schema="public"))
            self.assertIn("/empty/", [folder.path for folder in first.folders])
            self.assertEqual([asset.size for asset in first.assets], [4, 4])
            self.assertEqual([asset.content for asset in first.assets], list(contents.values()))
            with psycopg.connect(**admin) as connection:
                connection.execute("DELETE FROM wiki.tree WHERE tree = 'folder' AND \"fileName\" = 'empty'")
            snapshot = load_snapshot(database)
            settings = load_config(PROJECT / "config.json")
            report = run_scans(snapshot, settings, io.StringIO(), io.StringIO(), FILTER_FORBIDDEN)
            self.assertEqual(report.exit_code, 0)
            print("真实 PostgreSQL 二进制读取、同大小不同内容与全部 12 项扫描通过", flush=True)

            with psycopg.connect(**admin) as connection:
                connection.execute('UPDATE wiki.assets SET data = %s WHERE "fileName" = %s',
                                   (contents["a.png"], "b.png"))
            duplicate = duplicate_md5.run(ScanContext(load_snapshot(database)), {})
            self.assertFalse(duplicate.success)
            self.assertIn("MD5", duplicate.log_text)
            self.assertIn("/guides/images/a.png", duplicate.log_text)
            self.assertIn("/guides/images/b.png", duplicate.log_text)
            with psycopg.connect(**admin) as connection:
                connection.execute('UPDATE wiki.assets SET data = NULL WHERE "fileName" = %s', ("b.png",))
            with self.assertRaisesRegex(DatabaseError, r"/guides/images/b\.png"):
                load_snapshot(database)
            with psycopg.connect(**admin) as connection:
                connection.execute('UPDATE wiki.assets SET data = %s WHERE "fileName" = %s',
                                   (contents["b.png"], "b.png"))
                connection.execute('REVOKE SELECT (data) ON wiki.assets FROM scan_reader')
            with self.assertRaises(DatabaseError):
                load_snapshot(database)
            with psycopg.connect(**admin) as connection:
                connection.execute('GRANT SELECT (data) ON wiki.assets TO scan_reader')
            self.assertEqual(load_snapshot(database), snapshot)
            print("相同内容 MD5 检测、NULL 内容拒绝和 data 列权限验证通过", flush=True)

            custom_schema = 'Custom."Wiki'
            with psycopg.connect(**admin) as connection:
                connection.execute(sql.SQL("ALTER SCHEMA wiki RENAME TO {}").format(sql.Identifier(custom_schema)))
            database = dict(database, schema=custom_schema)
            self.assertEqual(load_snapshot(database), snapshot)
            print("wiki 与自定义 schema 读取通过", flush=True)

            docker("stop", source)
            with tempfile.TemporaryDirectory(prefix="wikijs-scan-integration-") as temporary:
                archive = Path(temporary) / "volume.tar.gz"
                with archive.open("wb") as output:
                    subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64",
                                    "--mount", f"type=volume,src={source_volume},dst=/backup,readonly",
                                    "--entrypoint", "tar", IMAGE, "--numeric-owner", "-czpf", "-", "-C", "/backup", "."],
                                   check=True, stdout=output, stderr=subprocess.PIPE)
                restored_port = free_port()
                restored_database = dict(database, port=restored_port)
                options = {"image": IMAGE, "platform": "linux/amd64", "volume_name": target_volume,
                           "container_name": target, "host_port": restored_port, "bind_host": "127.0.0.1",
                           "ready_timeout_seconds": 60}
                restore_database(archive, options, restored_database, progress=lambda message: print(message, flush=True))
                self.assertEqual(load_snapshot(restored_database), snapshot)
                print("停库备份、卷恢复和原凭据连接通过", flush=True)
        finally:
            for container in (source, target, "wikijs-scan-restore-" + target_volume):
                subprocess.run(["docker", "container", "rm", "--force", container], capture_output=True)
            for volume in (source_volume, target_volume):
                subprocess.run(["docker", "volume", "rm", volume], capture_output=True)

    @staticmethod
    def insert_tree(connection, site_id, record_id, path, kind):
        parent, _, name = path.rpartition("/")
        encoded = ".".join(segment.encode().hex() for segment in parent.split("/")) if parent else ""
        connection.execute('INSERT INTO tree VALUES (%s,%s::ltree,%s,%s,%s,%s,%s)',
                           (record_id, encoded, name, kind, "en", name, site_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
