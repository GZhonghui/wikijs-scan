"""真实字段形状与只读事务边界；测试数据不含任何媒体二进制。"""

import unittest
from unittest.mock import patch

from wikijs_scan.database import (
    ASSET_SQL, PAGE_SQL, SITE_SQL, TREE_SQL, DatabaseError,
    decode_tree_path, load_snapshot, snapshot_from_rows,
)


def encoded(path):
    return ".".join(part.encode("utf-8").hex() for part in path.split("/")) if path else ""


def tree_row(row_id, kind, path, *, title=None, locale="en"):
    parent, _, name = path.rpartition("/")
    return {"id": row_id, "kind": kind, "folder_path": encoded(parent),
            "file_name": name, "title": name if title is None else title, "locale": locale}


def page_row(row_id="p", path="guides/intro", **changes):
    return {"id": row_id, "path": path, "title": "intro", "content": "# intro\n",
            "content_type": "markdown", "locale": "en", **changes}


class DatabaseRowsTests(unittest.TestCase):
    def test_hex_paths_and_root(self):
        self.assertEqual(decode_tree_path(encoded("资料/images")), "资料/images")
        self.assertEqual(decode_tree_path(None), "")
        self.assertEqual(decode_tree_path(""), "")

    def test_invalid_encoded_paths_are_not_silently_repaired(self):
        for value in ("a", "gg", "61..62", "ff", "2f", "2e2e", "00"):
            with self.subTest(value=value), self.assertRaises(DatabaseError):
                decode_tree_path(value)

    def test_snapshot_preserves_empty_folders_and_media_size(self):
        tree = [tree_row("g", "folder", "guides"),
                tree_row("e", "folder", "empty"),
                tree_row("p", "page", "guides/intro"),
                tree_row("a", "asset", "guides/photo.png")]
        assets = [{"id": "a", "file_name": "photo.png", "file_size": 321}]
        snapshot = snapshot_from_rows([page_row()], tree, assets)
        self.assertEqual([f.path for f in snapshot.folders], ["/empty/", "/guides/"])
        self.assertEqual(snapshot.pages[0].path, "/guides/intro.md")
        self.assertEqual(snapshot.pages[0].content, "# intro\n")
        self.assertEqual((snapshot.assets[0].path, snapshot.assets[0].size), ("/guides/photo.png", 321))

    def test_same_name_page_and_folder_are_distinct(self):
        snapshot = snapshot_from_rows([page_row(path="guides")],
                                      [tree_row("g", "folder", "guides"),
                                       tree_row("p", "page", "guides")], [])
        self.assertEqual(snapshot.pages[0].path, "/guides.md")
        self.assertEqual(snapshot.folders[0].path, "/guides/")

    def test_source_extension_is_not_stripped_and_null_body_is_empty(self):
        snapshot = snapshot_from_rows([page_row(path="intro.md", content=None)],
                                      [tree_row("p", "page", "intro.md")], [])
        self.assertEqual(snapshot.pages[0].path, "/intro.md.md")
        self.assertEqual(snapshot.pages[0].content, "")

    def test_assets_without_size_remain_visible(self):
        snapshot = snapshot_from_rows([], [tree_row("a", "asset", "a.png")],
                                      [{"id": "a", "file_name": "a.png", "file_size": None}])
        self.assertIsNone(snapshot.assets[0].size)

    def test_mismatched_and_missing_records_fail(self):
        cases = [
            ([page_row(path="a")], [], []),
            ([], [tree_row("p", "page", "a")], []),
            ([], [], [{"id": "a", "file_name": "a.png", "file_size": 1}]),
            ([], [tree_row("a", "asset", "a.png")], []),
            ([page_row(path="a")], [tree_row("p", "page", "b")], []),
            ([page_row()], [tree_row("p", "page", "guides/intro")], []),
            ([], [tree_row("a", "asset", "a.png")],
             [{"id": "a", "file_name": "b.png", "file_size": 1}]),
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(DatabaseError):
                snapshot_from_rows(*rows)

    def test_wrong_language_or_content_type_fail(self):
        for changes in ({"locale": "zh"}, {"content_type": "html"}):
            with self.subTest(changes=changes), self.assertRaises(DatabaseError):
                snapshot_from_rows([page_row(path="a", **changes)], [tree_row("p", "page", "a")], [])
        with self.assertRaises(DatabaseError):
            snapshot_from_rows([], [tree_row("f", "folder", "a", locale="fr")], [])

    def test_duplicate_logical_files_fail(self):
        with self.assertRaises(DatabaseError):
            snapshot_from_rows([page_row(path="a")],
                               [tree_row("p", "page", "a"), tree_row("a", "asset", "a.md")],
                               [{"id": "a", "file_name": "a.md", "file_size": 1}])


class FakeConnection:
    def __init__(self, results):
        self.results = iter(results)
        self.queries = []
        self.read_only = False
        self.isolation_level = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        if not self.read_only or self.isolation_level is None:
            raise AssertionError("transaction must be read-only before the first query")
        self.queries.append((sql, params))

    def fetchall(self):
        return next(self.results)


class DatabaseConnectionTests(unittest.TestCase):
    def test_one_readonly_snapshot_and_explicit_metadata_columns(self):
        from psycopg import IsolationLevel

        connection = FakeConnection([[{"id": "site"}], [], [], []])
        with patch("psycopg.connect", return_value=connection) as connect:
            snapshot = load_snapshot({"host": "localhost", "port": 55432, "dbname": "wiki",
                                      "user": "wikijs", "password": "secret", "statement_timeout_ms": 1234})
        self.assertFalse(snapshot.file_paths)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.isolation_level, IsolationLevel.REPEATABLE_READ)
        self.assertEqual(connection.queries[0][1], ("1234",))
        self.assertEqual([query for query, _ in connection.queries[1:]],
                         [SITE_SQL, PAGE_SQL, TREE_SQL, ASSET_SQL])
        self.assertNotIn("statement_timeout_ms", connect.call_args.kwargs)
        self.assertNotIn("*", ASSET_SQL)
        self.assertNotIn("data", ASSET_SQL.lower())
        self.assertNotIn("preview", ASSET_SQL.lower())

    def test_site_count_is_a_hard_precondition(self):
        for sites in ([], [{"id": "a"}, {"id": "b"}]):
            connection = FakeConnection([sites])
            with patch("psycopg.connect", return_value=connection), self.assertRaises(DatabaseError):
                load_snapshot({})
            self.assertTrue(connection.closed)
            self.assertEqual(len(connection.queries), 2)

    def test_connection_errors_do_not_expose_passwords(self):
        import psycopg

        with patch("psycopg.connect", side_effect=psycopg.OperationalError("failed secret-value")):
            with self.assertRaises(DatabaseError) as raised:
                load_snapshot({"password": "secret-value"})
        self.assertNotIn("secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
