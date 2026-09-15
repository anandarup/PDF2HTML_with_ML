"""
Contract-snapshot tests for the public HTTP API.

Purpose (spec REQ-10): lock down the request/response SHAPES of the routes that
external integrators (CMS/iframe) depend on, so the scalable-deployment refactor
(state service, object storage, queue/worker split) can proceed without silently
breaking the contract.

These tests use Flask's test client — no running server, no OCI, and no Docling
model load. The heavy conversion path is exercised only through its cheap
validation branches and via a monkeypatched job store, so the suite runs fast
and offline.

Run:  python -m pytest tests/test_api_contract.py -q
      (or)  python -m unittest tests.test_api_contract
"""

from __future__ import annotations

import json
import unittest

import app as appmod


class ContractTestBase(unittest.TestCase):
    def setUp(self):
        appmod.app.config["TESTING"] = True
        self.client = appmod.app.test_client()

    # --- helpers -----------------------------------------------------------
    def assertKeysSuperset(self, obj: dict, keys: set, where: str):
        missing = keys - set(obj.keys())
        self.assertFalse(missing, f"{where}: missing keys {missing} (got {set(obj.keys())})")


class HealthContract(ContractTestBase):
    def test_healthz_shape(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"status": "ok"})

    def test_readyz_shape(self):
        r = self.client.get("/readyz")
        # default (file/local/thread) config should be ready
        self.assertIn(r.status_code, (200, 503))
        body = r.get_json()
        self.assertKeysSuperset(body, {"status", "checks"}, "/readyz")
        self.assertIn(body["status"], ("ok", "not_ready"))


class ConvertContract(ContractTestBase):
    def test_convert_no_file_returns_error_shape(self):
        r = self.client.post("/convert", data={})
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertKeysSuperset(body, {"error"}, "/convert no-file")

    def test_convert_non_pdf_returns_error_shape(self):
        import io
        data = {"pdf": (io.BytesIO(b"not a pdf"), "notes.txt")}
        r = self.client.post("/convert", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_convert_accepts_pdf_returns_job_id(self):
        """POST /convert must return 202 {job_id} without running conversion.

        We monkeypatch the background runner so no Docling/ML executes; we only
        assert the accepted-response contract.
        """
        import io

        started = {}

        # Prevent the real background thread (which would load ML libs) from running.
        orig_thread = appmod.threading.Thread

        class _NoopThread:
            def __init__(self, *a, **k):
                started["spawned"] = True

            def start(self):
                pass

        appmod.threading.Thread = _NoopThread
        try:
            data = {"pdf": (io.BytesIO(b"%PDF-1.4 minimal"), "sample.pdf")}
            r = self.client.post("/convert", data=data, content_type="multipart/form-data")
        finally:
            appmod.threading.Thread = orig_thread

        self.assertEqual(r.status_code, 202)
        body = r.get_json()
        self.assertKeysSuperset(body, {"job_id"}, "/convert accepted")
        self.assertIsInstance(body["job_id"], str)


class ConvertStatusContract(ContractTestBase):
    def test_unknown_job_returns_404_error_shape(self):
        r = self.client.get("/convert-status/does-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    def test_known_job_status_shape(self):
        """A job record must expose the fields integrators poll for."""
        # Seed a job through the existing writer so the shape is authoritative.
        job_id = "testjob1"
        with appmod.CONVERSION_JOBS_LOCK:
            appmod._write_job(job_id, {
                "status": "processing",
                "stage": "analyzing",
                "detail": "Analyzing…",
                "progress": {"page_count": 3, "image_count": 1},
                "result": None,
                "error": None,
                "ref_id": None,
                "edit_url": None,
                "render_url": None,
            })
        try:
            r = self.client.get(f"/convert-status/{job_id}")
            self.assertEqual(r.status_code, 200)
            body = r.get_json()
            self.assertKeysSuperset(
                body,
                {"status", "stage", "detail", "result", "error",
                 "ref_id", "edit_url", "render_url"},
                "/convert-status",
            )
        finally:
            appmod._job_path(job_id).unlink(missing_ok=True)


class LookupContract(ContractTestBase):
    def test_invalid_refid_format(self):
        r = self.client.get("/api/lookup/@@bad@@")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_unknown_refid(self):
        r = self.client.get("/api/lookup/11111111-1111-1111-1111-111111111111")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.get_json())

    def test_known_refid_shape(self):
        job_id = "testjob2"
        ref_id = "22222222-2222-2222-2222-222222222222"
        with appmod.CONVERSION_JOBS_LOCK:
            appmod._write_job(job_id, {
                "status": "done", "stage": "done", "detail": "done",
                "progress": {}, "error": None, "ref_id": ref_id,
                "edit_url": "/output/x/y.html", "render_url": None,
                "result": {"title": "T", "page_count": 2, "image_count": 1,
                           "html_url": "/output/x/y.html", "file_size": 10},
            })
        try:
            r = self.client.get(f"/api/lookup/{ref_id}")
            self.assertEqual(r.status_code, 200)
            body = r.get_json()
            # External contract keys (camelCase) integrators rely on.
            self.assertKeysSuperset(
                body,
                {"status", "editUrl", "renderUrl", "title", "pageCount", "imageCount"},
                "/api/lookup",
            )
            self.assertIn(body["status"],
                          ("converting", "ready-for-edit", "published", "error"))
        finally:
            appmod._job_path(job_id).unlink(missing_ok=True)


class SectionsContract(ContractTestBase):
    def test_sections_invalid_refid(self):
        r = self.client.get("/api/sections/@@bad@@")
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())


class PublishContract(ContractTestBase):
    def test_publish_missing_body(self):
        r = self.client.post("/publish", json={})
        # missing job_dir/filename -> 400 error shape (no OCI call happens)
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
