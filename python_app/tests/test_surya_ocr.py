"""
Tests for the Surya/Datalab OCR integration.

Two layers:
  1. Offline adapter tests (always run): verify the extract_pdf.py branch and
     the client's error handling without any network call, using stubs.
  2. Live smoke test (GATED): only runs when DATALAB_API_KEY is set in the
     environment. It performs one real submit->poll->result round-trip against
     the Datalab API to confirm auth and the response contract.

The live test is skipped by default so CI and offline runs stay green. To run
it:  DATALAB_API_KEY=... OCR_ENGINE=surya python -m pytest tests/test_surya_ocr.py -q

Run all:  python -m pytest tests/test_surya_ocr.py -q
"""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path


def _make_sample_pdf(path: str, text: str = "Surya Smoke Test Title") -> None:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    page.insert_text((72, 120), "Body paragraph for OCR round-trip.")
    doc.save(path)
    doc.close()


class SuryaClientErrorTests(unittest.TestCase):
    """Client error handling, no network."""

    def test_missing_key_raises(self):
        from tools.surya_ocr_client import convert_pdf, SuryaOcrError
        with self.assertRaises(SuryaOcrError):
            convert_pdf("x.pdf", api_key="", base_url="https://www.datalab.to")

    def test_eu_multipart_rejected_early(self):
        from tools.surya_ocr_client import _submit, SuryaOcrError
        import requests
        with self.assertRaises(SuryaOcrError):
            _submit(
                requests.Session(), "https://www.datalab.to", "KEY",
                "x.pdf", "balanced", "eu",
            )


class SuryaAdapterTests(unittest.TestCase):
    """extract_pdf_content's Surya branch, with the network stubbed out."""

    def setUp(self):
        self._prev_engine = os.environ.get("OCR_ENGINE")
        self._prev_key = os.environ.get("DATALAB_API_KEY")
        os.environ["OCR_ENGINE"] = "surya"
        os.environ["DATALAB_API_KEY"] = "TESTKEY"
        import importlib
        import config
        importlib.reload(config)

    def tearDown(self):
        if self._prev_engine is None:
            os.environ.pop("OCR_ENGINE", None)
        else:
            os.environ["OCR_ENGINE"] = self._prev_engine
        if self._prev_key is None:
            os.environ.pop("DATALAB_API_KEY", None)
        else:
            os.environ["DATALAB_API_KEY"] = self._prev_key
        import importlib
        import config
        importlib.reload(config)

    def test_adapter_builds_extraction_result(self):
        import tools.extract_pdf as ep
        import tools.surya_ocr_client as soc

        tmp = tempfile.mkdtemp()
        pdf_path = os.path.join(tmp, "sample.pdf")
        _make_sample_pdf(pdf_path)
        image_dir = os.path.join(tmp, "images")

        def fake_convert(path, **kw):
            return soc.SuryaOcrResult(
                markdown="# Hello\n\n![fig](figure-1.png)\n\nBody.",
                html="<h1>Hello</h1>",
                images={"figure-1.png": base64.b64encode(b"\x89PNG\r\n").decode()},
                page_count=2,
                request_id="req_test",
            )

        orig = soc.convert_pdf
        soc.convert_pdf = fake_convert
        try:
            res = ep.extract_pdf_content(
                pdf_file_path=pdf_path, image_output_dir=image_dir
            )
        finally:
            soc.convert_pdf = orig

        self.assertEqual(res.page_count, 2)
        self.assertIn("images/figure-1.png", res.markdown)
        self.assertTrue(res.image_paths)
        self.assertIn("Surya Smoke Test Title", res.first_page_text)
        self.assertEqual(Path(res.image_directory).name, "images")


@unittest.skipUnless(
    os.environ.get("DATALAB_API_KEY"),
    "live Datalab smoke test: set DATALAB_API_KEY to enable",
)
class SuryaLiveSmokeTest(unittest.TestCase):
    """One real round-trip against the Datalab API. Gated on DATALAB_API_KEY."""

    def test_live_round_trip(self):
        from tools.surya_ocr_client import convert_pdf
        import config

        tmp = tempfile.mkdtemp()
        pdf_path = os.path.join(tmp, "live.pdf")
        _make_sample_pdf(pdf_path, text="Live Round Trip Heading")

        result = convert_pdf(
            pdf_path,
            api_key=config.DATALAB_API_KEY,
            base_url=config.DATALAB_API_BASE_URL,
            mode=config.DATALAB_MODE,
            processing_location=config.DATALAB_PROCESSING_LOCATION,
            timeout_seconds=config.DATALAB_TIMEOUT_SECONDS,
            poll_interval=config.DATALAB_POLL_INTERVAL_SECONDS,
        )

        self.assertGreater(result.page_count, 0)
        self.assertTrue(result.markdown.strip(), "expected non-empty markdown")
        # The heading text should survive OCR/parse.
        self.assertIn("Heading", result.markdown)


if __name__ == "__main__":
    unittest.main()
