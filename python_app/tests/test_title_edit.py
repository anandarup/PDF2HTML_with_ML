"""
Tests for editing the auto-generated chapter title.

Covers the sanitizer, the HTML substitution (escaping + regex-replacement
safety), the PUT /output/<job_dir>/<filename> save contract, and the job-record
title sync. No OCI, no Docling, no real conversion.

Run:  python -m pytest tests/test_title_edit.py -q
      (or)  python -m unittest tests.test_title_edit
"""
from __future__ import annotations

import re
import unittest

import app as appmod

# A minimal stand-in for a converted document: the two places a title lives
# plus the body the existing save path rewrites.
DOC_HTML = (
    "<!DOCTYPE html>\n<html><head>\n"
    "  <title>Old Title</title>\n"
    "</head><body>\n"
    '  <header class="document-header">\n'
    '    <h1 class="document-title">Old Title</h1>\n'
    "  </header>\n"
    '  <article class="document-body"><p>body</p></article>\n'
    "</body></html>\n"
)


class SanitizeTitleTest(unittest.TestCase):
    def test_plain_text_passes_through(self):
        self.assertEqual(
            appmod.sanitize_document_title("Metals and Non-metals"),
            "Metals and Non-metals",
        )

    def test_whitespace_and_newlines_collapse(self):
        self.assertEqual(
            appmod.sanitize_document_title("  Chapter\n\t 2 \u00a0 Acids  "),
            "Chapter 2 Acids",
        )

    def test_markup_is_stripped(self):
        self.assertEqual(
            appmod.sanitize_document_title("Chapter <b>2</b> <em>Acids</em>"),
            "Chapter 2 Acids",
        )

    def test_markup_only_title_is_rejected(self):
        # Nothing but a payload => empty => caller keeps the previous title.
        self.assertEqual(
            appmod.sanitize_document_title('<img src=x onerror=alert(1)>'), ""
        )
        self.assertEqual(appmod.sanitize_document_title("<script>alert(1)</script>"), "alert(1)")

    def test_non_tag_angle_brackets_survive(self):
        # "<0>" / "a < b" are not markup; they must not be eaten by the tag
        # strip (they are HTML-escaped at insertion time instead).
        self.assertEqual(appmod.sanitize_document_title(r"Chapter \g<0>"), r"Chapter \g<0>")
        self.assertEqual(appmod.sanitize_document_title("When a < b"), "When a < b")

    def test_comments_are_stripped(self):
        self.assertEqual(
            appmod.sanitize_document_title("Ch <!-- hidden --> 4"), "Ch 4"
        )

    def test_empty_values_return_empty(self):
        for value in (None, "", "   ", "\n\t", "\u00a0"):
            self.assertEqual(appmod.sanitize_document_title(value), "")

    def test_length_is_capped(self):
        self.assertEqual(len(appmod.sanitize_document_title("x" * 5000)), 300)


class ApplyTitleTest(unittest.TestCase):
    def test_updates_heading_and_head_title(self):
        out, n = appmod._apply_document_title(DOC_HTML, "Acids, Bases and Salts")
        self.assertEqual(n, 1)
        self.assertIn('<h1 class="document-title">Acids, Bases and Salts</h1>', out)
        self.assertIn("<title>Acids, Bases and Salts</title>", out)
        self.assertNotIn("Old Title", out)

    def test_escapes_html_special_characters(self):
        out, _ = appmod._apply_document_title(DOC_HTML, 'Tom & Jerry\'s "Chapter"')
        self.assertIn(
            '<h1 class="document-title">Tom &amp; Jerry&#x27;s &quot;Chapter&quot;</h1>',
            out,
        )
        self.assertIn("<title>Tom &amp; Jerry&#x27;s &quot;Chapter&quot;</title>", out)

    def test_backslash_and_group_refs_are_literal(self):
        # re.sub replacement strings interpret \1 and \g<0>; a callable replacer
        # must keep them as literal characters.
        raw = r'Tom & Jerry \1 \g<0> C:\path'
        out, _ = appmod._apply_document_title(DOC_HTML, raw)
        self.assertIn(
            r'<h1 class="document-title">Tom &amp; Jerry \1 \g&lt;0&gt; C:\path</h1>',
            out,
        )

    def test_missing_heading_leaves_html_untouched(self):
        out, n = appmod._apply_document_title("<html><head><title>x</title></head></html>", "T")
        self.assertEqual(n, 0)
        self.assertIn("<title>x</title>", out)


class _FakeOutputStore:
    """In-memory stand-in for the output store (no disk, no bucket)."""

    def __init__(self, html):
        self.files = {("job1", "doc.html"): html}

    def read_html(self, job_dir, filename):
        return self.files.get((job_dir, filename))

    def write_html(self, job_dir, filename, content):
        self.files[(job_dir, filename)] = content


class SaveTitleRouteTest(unittest.TestCase):
    def setUp(self):
        appmod.app.config["TESTING"] = True
        self.client = appmod.app.test_client()
        self.store = _FakeOutputStore(DOC_HTML)
        self._real_store = appmod.output_store
        appmod.output_store = self.store

    def tearDown(self):
        appmod.output_store = self._real_store

    def _put(self, payload):
        return self.client.put(
            "/output/job1/doc.html",
            json=payload,
            content_type="application/json",
        )

    def saved_html(self):
        return self.store.files[("job1", "doc.html")]

    def test_body_only_save_leaves_title_alone(self):
        r = self._put({"body_html": "<p>new</p>"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("title", r.get_json())
        self.assertIn('<h1 class="document-title">Old Title</h1>', self.saved_html())

    def test_title_round_trip(self):
        r = self._put({"body_html": "<p>new</p>", "title": "Acids and Bases"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["title"], "Acids and Bases")
        html = self.saved_html()
        self.assertIn('<h1 class="document-title">Acids and Bases</h1>', html)
        self.assertIn("<title>Acids and Bases</title>", html)
        self.assertIn("<p>new</p>", html)
        # A second save reads back the persisted file and must be stable.
        r2 = self._put({"body_html": "<p>new</p>", "title": "Acids and Bases"})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(self.saved_html().count("Acids and Bases"), 2)

    def test_xss_payload_is_neutralised(self):
        r = self._put({
            "body_html": "<p>x</p>",
            "title": 'Chapter <img src=x onerror=alert(1)> One',
        })
        self.assertEqual(r.status_code, 200)
        html = self.saved_html()
        self.assertIn('<h1 class="document-title">Chapter One</h1>', html)
        self.assertNotIn("onerror", html)
        self.assertNotIn("<img", html)

    def test_markup_only_title_is_rejected(self):
        r = self._put({"body_html": "<p>x</p>", "title": "<img src=x onerror=alert(1)>"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        # Rejected before any write: the old title AND old body survive.
        self.assertIn('<h1 class="document-title">Old Title</h1>', self.saved_html())
        self.assertIn("<p>body</p>", self.saved_html())

    def test_blank_title_is_rejected(self):
        for value in ("", "   ", "\n"):
            r = self._put({"body_html": "<p>x</p>", "title": value})
            self.assertEqual(r.status_code, 400, f"blank title {value!r} must be rejected")
        self.assertIn('<h1 class="document-title">Old Title</h1>', self.saved_html())

    def test_job_record_title_is_synced(self):
        job_id = "job1"
        appmod._write_job(job_id, {
            "status": "done", "ref_id": "33333333-3333-3333-3333-333333333333",
            "result": {"title": "Old Title", "page_count": 1},
        })
        try:
            r = self._put({"body_html": "<p>x</p>", "title": "Brand New Title"})
            self.assertEqual(r.status_code, 200)
            job = appmod._read_job(job_id)
            self.assertEqual(job["result"]["title"], "Brand New Title")
            # Unrelated result fields are preserved.
            self.assertEqual(job["result"]["page_count"], 1)
        finally:
            appmod._job_path(job_id).unlink(missing_ok=True)


class StripEditorUiTest(unittest.TestCase):
    def test_learner_view_has_no_editable_title(self):
        from s3_publish import _strip_editor_ui

        editor_html = DOC_HTML.replace(
            '<h1 class="document-title">',
            '<h1 class="document-title" contenteditable="true" role="textbox"'
            ' aria-label="Chapter title — editable" tabindex="0" spellcheck="false">',
        )
        out = _strip_editor_ui(editor_html)
        # No editable regions anywhere in the learner page.
        self.assertNotIn("contenteditable", out)
        # And the title heading carries none of the editor's a11y affordances
        # (the reader shell injected by publish legitimately uses tabindex
        # elsewhere, so assert on the heading tag itself).
        heading = re.search(r'<h1[^>]*class="document-title"[^>]*>', out)
        self.assertIsNotNone(heading)
        for attr in ("contenteditable", "role=", "tabindex", "spellcheck", "aria-label"):
            self.assertNotIn(attr, heading.group(0))
        # The heading itself (and its text) survives for learners.
        self.assertIn('<h1 class="document-title">Old Title</h1>', out)


if __name__ == "__main__":
    unittest.main()
