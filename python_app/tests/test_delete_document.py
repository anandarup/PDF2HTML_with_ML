"""
Tests for DELETE /api/documents/<job_dir> (app.py) and delete_job.py.

Follows test_api_contract.py's convention: real Flask test client, real
job_store/output_store (LocalOutputStore + FileJobStore under the default
backends), no live OCI. Every test uses an obviously-fake, uniquely-prefixed
job_id/job_dir it creates itself, and removes it in a finally: block even on
failure -- these tests exercise a real filesystem delete, so leaving no trace
on a failed run matters more here than in most suites.

Run:  python -m pytest tests/test_delete_document.py -q
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

import app as appmod
import config
from delete_job import job_id_from_dir, delete_job_artifacts


class DeleteDocumentTestBase(unittest.TestCase):
    def setUp(self):
        appmod.app.config["TESTING"] = True
        self.client = appmod.app.test_client()
        # Never rely on a real DELETE_API_TOKEN being unset in this process --
        # make the test's intent explicit either way.
        self._orig_token = config.DELETE_API_TOKEN

    def tearDown(self):
        config.DELETE_API_TOKEN = self._orig_token

    def _make_job_dir(self, job_dir: str, *, with_source_pdf: bool = True) -> Path:
        d = config.OUTPUT_DIR / job_dir
        images = d / "images"
        images.mkdir(parents=True, exist_ok=True)
        (d / "media").mkdir(parents=True, exist_ok=True)
        (d / f"{job_dir}.html").write_text("<html></html>", encoding="utf-8")
        if with_source_pdf:
            (images / f"{job_dir}-source.pdf").write_bytes(b"%PDF-1.4")
        (d / "media" / "uploaded.png").write_bytes(b"\x89PNG")
        return d

    def _cleanup_dir(self, d: Path) -> None:
        if d.exists():
            shutil.rmtree(str(d))


class JobIdFromDirTest(unittest.TestCase):
    def test_splits_on_first_underscore(self):
        self.assertEqual(job_id_from_dir("ab12cd34_my file name.pdf-stem"), "ab12cd34")

    def test_no_underscore_falls_back_to_first_eight_chars(self):
        self.assertEqual(job_id_from_dir("ab12cd34xyz"), "ab12cd34")

    def test_matches_the_convention_already_used_in_app_py(self):
        # Pinned so this module and app.py's publish_for_learners /
        # _sync_job_record_title can never silently drift apart.
        job_dir = "deadbeef_Chap 5 \u2014 79-99"
        expected = job_dir.split("_")[0] if "_" in job_dir else job_dir[:8]
        self.assertEqual(job_id_from_dir(job_dir), expected)


class DeleteJobArtifactsTest(DeleteDocumentTestBase):
    """Exercises delete_job_artifacts() directly (below the HTTP layer)."""

    def test_removes_output_dir_upload_and_job_record(self):
        job_id = "zzdel001"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        upload = config.UPLOAD_DIR / f"{job_id}_test-doc.pdf"
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        upload.write_bytes(b"%PDF-1.4")
        appmod.job_store.create_job(job_id, {
            "status": "done", "stage": "done", "detail": "done",
            "progress": {}, "error": None, "ref_id": None,
            "edit_url": f"/output/{job_dir}/{job_dir}.html", "render_url": None,
            "result": {"title": "T"},
        })
        try:
            result = delete_job_artifacts(job_dir, appmod.job_store, appmod.output_store)
            self.assertTrue(result["output_dir_removed"])
            self.assertEqual(result["uploads_removed"], 1)
            self.assertTrue(result["job_record_removed"])
            self.assertFalse(d.exists())
            self.assertFalse(upload.exists())
            self.assertIsNone(appmod.job_store.get_job(job_id))
        finally:
            self._cleanup_dir(d)
            upload.unlink(missing_ok=True)
            appmod.job_store.delete_job(job_id)

    def test_missing_everything_is_not_an_error(self):
        # No output dir, no upload, no job record -- every step is false/0,
        # nothing raises. This is the "already deleted, or never existed"
        # case the route treats as 404 rather than 500.
        result = delete_job_artifacts("zzdel-ghost_nope", appmod.job_store, appmod.output_store)
        self.assertFalse(result["output_dir_removed"])
        self.assertEqual(result["uploads_removed"], 0)
        self.assertFalse(result["job_record_removed"])

    def test_oci_step_is_a_no_op_when_oci_is_unavailable(self):
        # This box turns out to genuinely be an OCI instance with working
        # instance-principal credentials (is_available() is True here), so
        # this test forces the unavailable case explicitly rather than assume
        # anything about the environment it runs in -- it must pass the same
        # way on a laptop with no metadata endpoint at all.
        import oci_storage
        job_id = "zzdel002"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        orig_is_available = oci_storage.is_available
        oci_storage.is_available = lambda: False
        try:
            result = delete_job_artifacts(job_dir, appmod.job_store, appmod.output_store)
            self.assertFalse(result["oci"]["attempted"])
            self.assertEqual(result["oci"]["buckets"], {})
        finally:
            oci_storage.is_available = orig_is_available
            self._cleanup_dir(d)

    def test_oci_step_deletes_by_prefix_in_all_three_buckets_when_available(self):
        # The inverse case, also forced explicitly and with delete_prefix
        # itself replaced by a recording stub -- this must NEVER call the
        # real OCI SDK against the real buckets from a test.
        import oci_storage
        job_id = "zzdel003"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        calls = []

        def fake_delete_prefix(prefix, bucket=None):
            calls.append((prefix, bucket))
            return {"deleted": 0, "failed": []}

        orig_is_available = oci_storage.is_available
        orig_delete_prefix = oci_storage.delete_prefix
        oci_storage.is_available = lambda: True
        oci_storage.delete_prefix = fake_delete_prefix
        try:
            result = delete_job_artifacts(job_dir, appmod.job_store, appmod.output_store)
            self.assertTrue(result["oci"]["attempted"])
            self.assertEqual(set(result["oci"]["buckets"].keys()), {"html", "media", "video"})
            # Every call must be scoped to this job's own prefix -- never a
            # bare/empty prefix, which would match every object in the bucket.
            self.assertEqual(len(calls), 3)
            for prefix, _bucket in calls:
                self.assertEqual(prefix, f"{job_dir}/")
        finally:
            oci_storage.is_available = orig_is_available
            oci_storage.delete_prefix = orig_delete_prefix
            self._cleanup_dir(d)


class DeleteDocumentRouteTest(DeleteDocumentTestBase):
    """Exercises the HTTP route end to end via the Flask test client."""

    def test_deletes_a_done_job_and_returns_200(self):
        config.DELETE_API_TOKEN = ""  # no token configured -- check is skipped
        job_id = "zzdel010"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        appmod.job_store.create_job(job_id, {"status": "done", "result": {}})
        try:
            r = self.client.delete(f"/api/documents/{job_dir}")
            self.assertEqual(r.status_code, 200)
            body = r.get_json()
            self.assertTrue(body["success"])
            self.assertTrue(body["output_dir_removed"])
            self.assertFalse(d.exists())
            self.assertIsNone(appmod.job_store.get_job(job_id))
        finally:
            self._cleanup_dir(d)
            appmod.job_store.delete_job(job_id)

    def test_deletes_a_published_job_too_unlike_the_retention_cleanup(self):
        # cleanup_job.py's _is_published guard NEVER deletes published content
        # (invariant I8). This route is the opposite: an explicit request to
        # delete overrides that, published or not -- confirm it actually does.
        config.DELETE_API_TOKEN = ""
        job_id = "zzdel011"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        appmod.job_store.create_job(job_id, {
            "status": "published", "render_url": "https://example.invalid/x.html",
            "result": {},
        })
        try:
            r = self.client.delete(f"/api/documents/{job_dir}")
            self.assertEqual(r.status_code, 200)
            self.assertFalse(d.exists())
        finally:
            self._cleanup_dir(d)
            appmod.job_store.delete_job(job_id)

    def test_processing_job_returns_409_and_does_not_delete(self):
        config.DELETE_API_TOKEN = ""
        job_id = "zzdel012"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        appmod.job_store.create_job(job_id, {"status": "processing"})
        try:
            r = self.client.delete(f"/api/documents/{job_dir}")
            self.assertEqual(r.status_code, 409)
            self.assertIn("error", r.get_json())
            self.assertTrue(d.exists())  # untouched
            self.assertIsNotNone(appmod.job_store.get_job(job_id))
        finally:
            self._cleanup_dir(d)
            appmod.job_store.delete_job(job_id)

    def test_unknown_job_dir_with_nothing_on_disk_returns_404(self):
        config.DELETE_API_TOKEN = ""
        r = self.client.delete("/api/documents/zzdel-ghost_nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    def test_traversal_attempt_rejected(self):
        config.DELETE_API_TOKEN = ""
        r = self.client.delete("/api/documents/..%2f..%2fetc")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_missing_token_rejected_when_token_is_configured(self):
        config.DELETE_API_TOKEN = "s3cr3t"
        job_id = "zzdel013"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        appmod.job_store.create_job(job_id, {"status": "done"})
        try:
            r = self.client.delete(f"/api/documents/{job_dir}")
            self.assertEqual(r.status_code, 401)
            self.assertTrue(d.exists())  # rejected before any deletion happens
        finally:
            self._cleanup_dir(d)
            appmod.job_store.delete_job(job_id)

    def test_correct_token_is_accepted(self):
        config.DELETE_API_TOKEN = "s3cr3t"
        job_id = "zzdel014"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        appmod.job_store.create_job(job_id, {"status": "done"})
        try:
            r = self.client.delete(
                f"/api/documents/{job_dir}",
                headers={"X-Delete-Token": "s3cr3t"},
            )
            self.assertEqual(r.status_code, 200)
            self.assertFalse(d.exists())
        finally:
            self._cleanup_dir(d)
            appmod.job_store.delete_job(job_id)

    def test_wrong_token_rejected(self):
        config.DELETE_API_TOKEN = "s3cr3t"
        job_id = "zzdel015"
        job_dir = f"{job_id}_test-doc"
        d = self._make_job_dir(job_dir)
        appmod.job_store.create_job(job_id, {"status": "done"})
        try:
            r = self.client.delete(
                f"/api/documents/{job_dir}",
                headers={"X-Delete-Token": "wrong"},
            )
            self.assertEqual(r.status_code, 401)
            self.assertTrue(d.exists())
        finally:
            self._cleanup_dir(d)
            appmod.job_store.delete_job(job_id)


if __name__ == "__main__":
    unittest.main()
