"""
Tests for storage.output_store.OciOutputStore.

The OCI backend was previously untested. These cover two fixes:

  * serve() built its redirect URL with get_public_url's DEFAULT bucket (media),
    while write_html uploads to the HTML bucket. Under OUTPUT_BACKEND=oci that
    handed out a 404 URL for every document.

  * read_html had no bucket fallback, so it returned None on any replica whose
    local mirror was absent -- a fresh pod, or a mirror aged out by the
    retention cronjob.

A fake storage module is injected, so nothing here touches OCI.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.output_store import OciOutputStore, ServeResult  # noqa: E402


class FakeOciStorage:
    """Stand-in for the oci_storage module."""

    BUCKET_NAME = "media-bucket"
    HTML_BUCKET_NAME = "html-bucket"

    def __init__(self, objects=None, fail=False):
        self.objects = dict(objects or {})
        self.fail = fail
        self.url_calls = []
        self.get_text_calls = []
        self.uploads = []

    def get_public_url(self, object_name, bucket=None):
        if self.fail:
            raise RuntimeError("OCI unreachable")
        self.url_calls.append((object_name, bucket))
        return f"https://obj.example/n/ns/b/{bucket or self.BUCKET_NAME}/o/{object_name}"

    def get_text(self, object_name, bucket=None, encoding="utf-8"):
        if self.fail:
            raise RuntimeError("OCI unreachable")
        self.get_text_calls.append((object_name, bucket))
        return self.objects.get(object_name)

    def upload_html_to_bucket(self, local_path, object_name):
        self.uploads.append(object_name)
        return f"https://obj.example/{object_name}"


def _store(fake, mirror=None, uploads=None):
    store = OciOutputStore(types.SimpleNamespace(), local_mirror=mirror, upload_dir=uploads)
    store._oci = lambda: fake
    return store


class ServeTest(unittest.TestCase):

    def test_redirect_targets_the_html_bucket(self):
        """Regression guard: the media bucket 404s for HTML objects."""
        fake = FakeOciStorage()
        res = _store(fake).serve("job1", "doc.html")
        self.assertEqual(res.kind, "redirect")
        self.assertIn("/b/html-bucket/", res.url)
        self.assertNotIn("/b/media-bucket/", res.url)
        self.assertEqual(fake.url_calls, [("job1/doc.html", "html-bucket")])

    def test_object_name_is_job_dir_slash_filename(self):
        fake = FakeOciStorage()
        _store(fake).serve("09111944_Chap 5", "x.html")
        self.assertEqual(fake.url_calls[0][0], "09111944_Chap 5/x.html")

    def test_falls_back_to_mirror_when_oci_unreachable(self):
        root = Path(tempfile.mkdtemp())
        res = _store(FakeOciStorage(fail=True), mirror=root).serve("job1", "doc.html")
        self.assertEqual(res.kind, "file")
        self.assertIn("job1", res.directory)

    def test_missing_when_oci_unreachable_and_no_mirror(self):
        res = _store(FakeOciStorage(fail=True)).serve("job1", "doc.html")
        self.assertEqual(res.kind, "missing")


class ReadHtmlTest(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def _seed_mirror(self, job_dir, filename, text):
        d = self.root / job_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(text, encoding="utf-8")

    def test_prefers_local_mirror(self):
        self._seed_mirror("job1", "doc.html", "<p>from mirror</p>")
        fake = FakeOciStorage(objects={"job1/doc.html": "<p>from bucket</p>"})
        got = _store(fake, mirror=self.root).read_html("job1", "doc.html")
        self.assertEqual(got, "<p>from mirror</p>")
        self.assertEqual(fake.get_text_calls, [])   # bucket not consulted

    def test_falls_back_to_bucket_when_mirror_file_absent(self):
        fake = FakeOciStorage(objects={"job1/doc.html": "<p>from bucket</p>"})
        got = _store(fake, mirror=self.root).read_html("job1", "doc.html")
        self.assertEqual(got, "<p>from bucket</p>")
        self.assertEqual(fake.get_text_calls, [("job1/doc.html", "html-bucket")])

    def test_reads_bucket_when_no_mirror_configured(self):
        fake = FakeOciStorage(objects={"job1/doc.html": "<p>bucket only</p>"})
        self.assertEqual(
            _store(fake).read_html("job1", "doc.html"), "<p>bucket only</p>"
        )

    def test_none_when_object_absent(self):
        fake = FakeOciStorage(objects={})
        self.assertIsNone(_store(fake).read_html("job1", "doc.html"))

    def test_none_rather_than_raising_when_storage_fails(self):
        self.assertIsNone(_store(FakeOciStorage(fail=True)).read_html("j", "d.html"))

    def test_traversal_rejected_without_touching_the_bucket(self):
        fake = FakeOciStorage(objects={"../etc/passwd": "secret"})
        self.assertIsNone(_store(fake).read_html("..", "etc/passwd"))
        self.assertEqual(fake.get_text_calls, [])

    def test_unicode_content_survives_the_bucket_path(self):
        fake = FakeOciStorage(objects={"j/d.html": '<h2 id="अभ्यास">अभ्यास</h2>'})
        self.assertIn("अभ्यास", _store(fake).read_html("j", "d.html"))


if __name__ == "__main__":
    unittest.main()
