"""验证归档与 Docker 调用边界；所有 Docker 操作均模拟，不接触任何真实卷。"""

import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from wikijs_scan.restore import (
    RESTORE_DEFAULTS, RestoreError, _ensure_empty_volume, _ensure_image, _inspect,
    _wait_for_database, restore_database, validate_archive, validate_restore_config,
)


def archive_at(path, extra=(), version=b"18\n", root_mode=0o700):
    with tarfile.open(path, "w:gz") as archive:
        for name in (".", "18", "18/docker", "18/docker/global", "18/docker/base"):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = root_mode if name == "." else 0o700
            archive.addfile(member)
        for name, content in (("18/docker/PG_VERSION", version),
                              ("18/docker/global/pg_control", b"control")):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o600
            member.uid = member.gid = 999
            archive.addfile(member, io.BytesIO(content))
        for member in extra:
            archive.addfile(member)


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["docker"], returncode, stdout, stderr)


DATABASE = {"host": "127.0.0.1", "port": 55432, "dbname": "wiki", "user": "wikijs",
            "password": "test-password", "connect_timeout": 5, "sslmode": "prefer"}


class TemporaryArchiveCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = Path(self.temporary.name) / "wiki.tar.gz"


class ArchiveTests(TemporaryArchiveCase):
    def test_valid_pg18_archive_without_extracting(self):
        archive_at(self.archive)
        info = validate_archive(self.archive)
        self.assertEqual(info.members, 7)
        self.assertEqual(info.file_bytes, 10)
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [self.archive])

    def test_path_escape_links_and_devices_rejected(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                           ("18/../escape", tarfile.REGTYPE), ("18\\escape", tarfile.REGTYPE),
                           ("18/docker/link", tarfile.SYMTYPE), ("18/docker/hard", tarfile.LNKTYPE),
                           ("18/docker/device", tarfile.CHRTYPE), ("18/docker/pipe", tarfile.FIFOTYPE)):
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "../../escape"
            archive_at(self.archive, [member])
            with self.subTest(name=name), self.assertRaises(RestoreError):
                validate_archive(self.archive)

    def test_duplicate_or_conflicting_paths_rejected(self):
        for names in (("18/docker/PG_VERSION",), ("18/docker/a/b", "18/docker/a"),
                      ("18/docker/a", "18/docker/a/b")):
            archive_at(self.archive, [tarfile.TarInfo(name) for name in names])
            with self.subTest(names=names), self.assertRaises(RestoreError):
                validate_archive(self.archive)

    def test_wrong_version_and_truncated_archive_rejected(self):
        archive_at(self.archive, version=b"17\n")
        with self.assertRaises(RestoreError):
            validate_archive(self.archive)
        archive_at(self.archive)
        self.archive.write_bytes(self.archive.read_bytes()[:-6])
        with self.assertRaises(RestoreError):
            validate_archive(self.archive)

    def test_unrelated_archive_rejected(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.addfile(tarfile.TarInfo("hello.txt"))
        with self.assertRaises(RestoreError):
            validate_archive(self.archive)

    def test_sticky_and_setgid_directory_modes_are_preserved(self):
        extra = []
        for name, mode in (("18/docker/pg_stat", 0o1777), ("18/docker/pg_tblspc", 0o2750)):
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            member.mode = mode
            extra.append(member)
        archive_at(self.archive, extra, root_mode=0o1777)
        self.assertEqual(validate_archive(self.archive).members, 9)

    def test_setuid_regular_files_are_rejected(self):
        member = tarfile.TarInfo("18/docker/unexpected")
        member.mode = 0o4755
        archive_at(self.archive, [member])
        with self.assertRaises(RestoreError):
            validate_archive(self.archive)


class RestoreTests(TemporaryArchiveCase):
    def test_configuration(self):
        self.assertEqual(validate_restore_config({}, DATABASE)["host_port"], 55432)
        for changes in ({"host_port": 5432}, {"platform": "linux/arm64"},
                        {"bind_host": "bad hostname"}, {"volume_name": "-bad"},
                        {"ready_timeout_seconds": False}, {"unknown": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_restore_config(changes, DATABASE)

    def test_success_preserves_pg18_layout_and_uses_no_initialization_password(self):
        archive_at(self.archive)
        calls = []

        def docker(args, **kwargs):
            calls.append(args)
            if "--interactive" in args:
                self.assertEqual(kwargs["stdin"].read(2), b"\x1f\x8b")
            return completed()

        with patch("wikijs_scan.restore._docker", side_effect=docker), \
                patch("wikijs_scan.restore._inspect", return_value=None), \
                patch("wikijs_scan.restore._ensure_image"), \
                patch("wikijs_scan.restore._ensure_empty_volume"), \
                patch("wikijs_scan.restore._wait_for_database") as wait:
            summary = restore_database(self.archive, {}, DATABASE)
        start = next(args for args in calls if "--detach" in args)
        self.assertIn("type=volume,src=wiki-prod_db-data,dst=/var/lib/postgresql", start)
        self.assertIn("127.0.0.1:55432:5432", start)
        self.assertNotIn("--env", start)
        self.assertNotIn(DATABASE["password"], " ".join(sum(calls, [])))
        wait.assert_called_once()
        self.assertIn("恢复成功", summary)

    def test_existing_container_is_never_overwritten(self):
        archive_at(self.archive)
        with patch("wikijs_scan.restore._docker", return_value=completed()) as docker, \
                patch("wikijs_scan.restore._inspect", return_value={"Id": "existing"}), \
                self.assertRaisesRegex(RestoreError, "容器已存在"):
            restore_database(self.archive, {}, DATABASE)
        self.assertEqual(docker.call_count, 1)

    def test_bad_archive_causes_no_docker_actions(self):
        self.archive.write_bytes(b"not a backup")
        with patch("wikijs_scan.restore._docker") as docker, self.assertRaises(RestoreError):
            restore_database(self.archive, {}, DATABASE)
        docker.assert_not_called()

    def test_nonempty_or_used_volume_is_never_cleared(self):
        for results in ([completed("existing-container")], [completed(), completed("/restore/data")]):
            with patch("wikijs_scan.restore._docker", side_effect=results) as docker, \
                    patch("wikijs_scan.restore._inspect", return_value={"Driver": "local", "Options": None}), \
                    self.assertRaises(RestoreError):
                _ensure_empty_volume(RESTORE_DEFAULTS)
            self.assertFalse(any("rm" in call.args[0] for call in docker.call_args_list))

    def test_host_bound_volume_is_rejected(self):
        with patch("wikijs_scan.restore._docker", return_value=completed()), \
                patch("wikijs_scan.restore._inspect", return_value={"Driver": "local", "Options": {"device": "/srv"}}), \
                self.assertRaises(RestoreError):
            _ensure_empty_volume(RESTORE_DEFAULTS)

    def test_docker_permission_errors_are_not_treated_as_absent(self):
        with patch("wikijs_scan.restore._docker", return_value=completed(stderr="permission denied", returncode=1)), \
                self.assertRaises(RestoreError):
            _inspect("volume", "example")

    def test_same_architecture_and_pg_major_required(self):
        with patch("wikijs_scan.restore._inspect", return_value={"Architecture": "arm64", "Os": "linux"}), \
                self.assertRaises(RestoreError):
            _ensure_image(RESTORE_DEFAULTS)
        with patch("wikijs_scan.restore._inspect", return_value={"Architecture": "amd64", "Os": "linux"}), \
                patch("wikijs_scan.restore._docker", return_value=completed("postgres (PostgreSQL) 17.4")), \
                self.assertRaises(RestoreError):
            _ensure_image(RESTORE_DEFAULTS)

    def test_readiness_verifies_the_actual_configured_credentials(self):
        with patch("wikijs_scan.restore._docker", return_value=completed()) as docker, \
                patch("wikijs_scan.restore._verify_connection") as verify:
            _wait_for_database(RESTORE_DEFAULTS, DATABASE)
        self.assertIn("pg_isready", docker.call_args.args[0])
        verify.assert_called_once_with(DATABASE)

    def test_wrong_credentials_fail_without_exposing_password(self):
        with patch("wikijs_scan.restore._docker", return_value=completed()), \
                patch("wikijs_scan.restore._verify_connection", side_effect=ValueError("test-password refused")), \
                self.assertRaises(RestoreError) as raised:
            _wait_for_database(RESTORE_DEFAULTS, DATABASE)
        self.assertNotIn("test-password", str(raised.exception))
        self.assertIn("原有凭据", str(raised.exception))

    def test_readiness_timeout_preserves_container_and_volume(self):
        config = {**RESTORE_DEFAULTS, "ready_timeout_seconds": 1}
        with patch("wikijs_scan.restore._docker", return_value=completed(returncode=1)) as docker, \
                patch("wikijs_scan.restore.time.monotonic", side_effect=[0, 0, 0.5, 2]), \
                patch("wikijs_scan.restore.time.sleep"), \
                self.assertRaisesRegex(RestoreError, "已保留容器与数据卷"):
            _wait_for_database(config, DATABASE)
        self.assertTrue(all(call.args[0][0] == "exec" for call in docker.call_args_list))


if __name__ == "__main__":
    unittest.main()
