from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.file_hashes import MediaHashService


class MediaHashServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.media = self.base / "example.mkv"
        self.media.write_bytes(b"first version")
        stat = self.media.stat()
        with self.database.connect() as conn:
            root_id = conn.execute(
                "INSERT INTO roots(path,kind,label) VALUES (?, 'movie', 'Movies')",
                (str(self.base),),
            ).lastrowid
            title_id = conn.execute(
                """INSERT INTO titles(root_id,kind,title,folder_path)
                   VALUES (?, 'movie', 'Example', ?)""",
                (root_id, str(self.media)),
            ).lastrowid
            self.file_id = conn.execute(
                """INSERT INTO files(
                     title_id,path,filename,extension,size_bytes,modified_at,seen_scan
                   ) VALUES (?,?,?,?,?,?, 'test')""",
                (title_id, str(self.media), self.media.name, ".mkv",
                 stat.st_size, stat.st_mtime),
            ).lastrowid
        self.service = MediaHashService(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_hash_is_persisted_and_current_file_is_not_requeued(self):
        self.assertEqual(self.service.queue([self.file_id]), [self.file_id])
        digest = self.service.hash_file(self.file_id)
        self.assertEqual(digest, hashlib.sha256(b"first version").hexdigest())
        self.assertEqual(self.service.queue([self.file_id]), [])
        record = self.service.records()[self.file_id]
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["sha256"], digest)

    def test_changed_file_is_requeued_and_rehashed(self):
        first = self.service.hash_file(self.file_id)
        self.media.write_bytes(b"a different and longer second version")
        changed = self.media.stat()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE files SET size_bytes=?,modified_at=? WHERE id=?",
                (changed.st_size, changed.st_mtime, self.file_id),
            )
        self.assertEqual(self.service.queue([self.file_id]), [self.file_id])
        second = self.service.hash_file(self.file_id)
        self.assertNotEqual(first, second)
        self.assertEqual(
            second,
            hashlib.sha256(b"a different and longer second version").hexdigest(),
        )

    def test_interrupted_running_hash_returns_to_queue_on_restart(self):
        self.service.queue([self.file_id])
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE media_file_hashes SET status='running' WHERE file_id=?",
                (self.file_id,),
            )
        restarted = MediaHashService(self.database)
        record = restarted.records()[self.file_id]
        self.assertEqual(record["status"], "queued")
        self.assertIn("interrupted", record["error"].casefold())

    def test_import_limit_can_split_one_batch_at_two_hundred(self):
        ids = list(range(1, 251))
        immediate, deferred = ids[:200], ids[200:]
        self.assertEqual(len(immediate), 200)
        self.assertEqual(len(deferred), 50)
        self.assertEqual(immediate[-1], 200)
        self.assertEqual(deferred[0], 201)


if __name__ == "__main__":
    unittest.main()
