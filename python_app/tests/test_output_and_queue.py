"""
Tests for Phase 3 (output_store) and Phase 4 (job_queue) seams — default
backends only (LocalOutputStore, ThreadQueue), which must reproduce current
behavior. No OCI, no live queue.

Run:  python -m unittest tests.test_output_and_queue -v
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from storage.output_store import LocalOutputStore, ServeResult
from queue_backend.job_queue import ThreadQueue


class LocalOutputStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.out = self.root / "output"
        self.up = self.root / "uploads"
        self.out.mkdir(); self.up.mkdir()
        self.store = LocalOutputStore(self.out, self.up)

    def test_serve_is_file_action(self):
        res = self.store.serve("job1", "doc.html")
        self.assertIsInstance(res, ServeResult)
        self.assertEqual(res.kind, "file")
        self.assertTrue(res.directory.endswith("output/job1"))
        self.assertEqual(res.filename, "doc.html")

    def test_read_write_html_roundtrip(self):
        d = self.out / "job1"; d.mkdir()
        (d / "doc.html").write_text("<article class=\"document-body\">x</article>", encoding="utf-8")
        self.assertIn("document-body", self.store.read_html("job1", "doc.html"))
        self.store.write_html("job1", "doc.html", "<article class=\"document-body\">y</article>")
        self.assertIn(">y<", self.store.read_html("job1", "doc.html"))

    def test_read_missing_returns_none(self):
        self.assertIsNone(self.store.read_html("nope", "x.html"))

    def test_traversal_rejected(self):
        # read_html must not escape the output root
        self.assertIsNone(self.store.read_html("..", "etc/passwd"))

    def test_put_upload_bytes(self):
        ref = self.store.put_upload("abc_sample.pdf", b"%PDF-1.4")
        self.assertTrue(Path(ref).exists())
        self.assertEqual(Path(ref).read_bytes(), b"%PDF-1.4")


class ThreadQueueTest(unittest.TestCase):
    def test_enqueue_runs_handler_in_thread(self):
        done = threading.Event()
        seen = {}

        def handler(msg):
            seen.update(msg)
            done.set()

        q = ThreadQueue(handler)
        q.enqueue({"job_id": "j1", "upload_ref": "/tmp/x.pdf"})
        self.assertTrue(done.wait(timeout=5), "handler did not run")
        self.assertEqual(seen["job_id"], "j1")

    def test_handler_exception_does_not_propagate(self):
        ran = threading.Event()

        def boom(msg):
            ran.set()
            raise RuntimeError("kaboom")

        q = ThreadQueue(boom)
        # Must not raise on the caller's thread.
        q.enqueue({"job_id": "j2"})
        self.assertTrue(ran.wait(timeout=5))
        time.sleep(0.05)  # let the thread finish swallowing the error

    def test_consume_is_noop(self):
        q = ThreadQueue(lambda m: None)
        self.assertIsNone(q.consume(lambda m: None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
