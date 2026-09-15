"""
Output/upload storage abstraction.

Phase 3 of the scalable-deployment plan puts rendered output, edited HTML, and
uploads behind an `OutputStore` seam so the app never assumes local disk. The
default `LocalOutputStore` reproduces the current behavior exactly (files under
OUTPUT_DIR / UPLOAD_DIR, served via send_from_directory). `OciOutputStore`
serves rendered content from OCI Object Storage (redirect to the public/CDN URL)
and writes edits back to the bucket, so multiple replicas serve identical
content. The backend is selected by `config.OUTPUT_BACKEND` (local | oci).

Design notes:
- Rendered HTML + images are ALREADY uploaded to OCI during conversion (see
  convert.py). So under `oci`, serving is a redirect and edit-save is a
  read-modify-write of the HTML object; no duplication of the conversion path.
- The HTTP-facing helpers return small "action" descriptors
  (serve_file / redirect) so app.py stays framework-thin and testable without a
  live bucket.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ServeResult:
    """What the HTTP layer should do to serve an output file.

    kind == "file"     -> send `path` from `directory` (send_from_directory)
    kind == "redirect" -> 302 to `url`
    kind == "missing"  -> 404
    """
    kind: str
    directory: Optional[str] = None
    filename: Optional[str] = None
    url: Optional[str] = None


class OutputStore(abc.ABC):
    @abc.abstractmethod
    def serve(self, job_dir: str, filename: str) -> ServeResult:
        """Describe how to serve a rendered output file."""

    @abc.abstractmethod
    def read_html(self, job_dir: str, filename: str) -> Optional[str]:
        """Return the HTML text of a rendered document, or None if missing."""

    @abc.abstractmethod
    def write_html(self, job_dir: str, filename: str, html: str) -> None:
        """Persist edited HTML for a rendered document."""

    @abc.abstractmethod
    def put_upload(self, name: str, data) -> str:
        """Store a transient upload (e.g. the incoming PDF). Returns a
        backend-specific reference the worker can later read."""

    @abc.abstractmethod
    def local_output_root(self) -> Optional[Path]:
        """Return the local output root if this backend uses one (else None).
        Used by routes that still resolve local paths (media, sections)."""


def _safe_join(root: Path, job_dir: str, filename: str) -> Optional[Path]:
    """Resolve root/job_dir/filename, rejecting traversal outside root."""
    p = (root.resolve() / job_dir / filename)
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return p


class LocalOutputStore(OutputStore):
    """Local-disk output store — identical to the app's original behavior."""

    def __init__(self, output_dir: Path, upload_dir: Path):
        self._output = Path(output_dir)
        self._uploads = Path(upload_dir)

    def serve(self, job_dir: str, filename: str) -> ServeResult:
        directory = self._output.resolve() / job_dir
        return ServeResult(kind="file", directory=str(directory), filename=filename)

    def read_html(self, job_dir: str, filename: str) -> Optional[str]:
        p = _safe_join(self._output, job_dir, filename)
        if p is None or not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def write_html(self, job_dir: str, filename: str, html: str) -> None:
        p = _safe_join(self._output, job_dir, filename)
        if p is None:
            raise ValueError("Invalid path")
        p.write_text(html, encoding="utf-8")

    def put_upload(self, name: str, data) -> str:
        self._uploads.mkdir(parents=True, exist_ok=True)
        dest = self._uploads / name
        # `data` is a Werkzeug FileStorage or a path/bytes; handle FileStorage.
        if hasattr(data, "save"):
            data.save(str(dest))
        elif isinstance(data, (bytes, bytearray)):
            dest.write_bytes(data)
        else:
            # a path-like source
            dest.write_bytes(Path(data).read_bytes())
        return str(dest)

    def local_output_root(self) -> Optional[Path]:
        return self._output


class OciOutputStore(OutputStore):
    """
    OCI Object Storage output store.

    Rendered HTML/images are served by redirecting to their public bucket URL
    (CDN-frontable). Edited HTML is written back to the HTML bucket. Uploads go
    to a transient `uploads/` prefix. Falls back to a local mirror for reads
    when present (rendered output is also written locally during conversion),
    which keeps section-parsing routes working without extra bucket round-trips.
    """

    def __init__(self, config_module, local_mirror: Optional[Path] = None,
                 upload_dir: Optional[Path] = None):
        self._cfg = config_module
        self._mirror = Path(local_mirror) if local_mirror else None
        self._uploads = Path(upload_dir) if upload_dir else None

    def _oci(self):
        # Lazy import so the OCI SDK / bucket access is only needed under `oci`.
        import oci_storage
        return oci_storage

    def _html_object_name(self, job_dir: str, filename: str) -> str:
        return f"{job_dir}/{filename}"

    def serve(self, job_dir: str, filename: str) -> ServeResult:
        # Redirect to the public bucket URL (CDN edge in production). The HTML
        # bucket is public (ObjectRead); the object was uploaded at conversion.
        try:
            oci_storage = self._oci()
            url = oci_storage.get_public_url(self._html_object_name(job_dir, filename))
            return ServeResult(kind="redirect", url=url)
        except Exception:
            # If OCI is unreachable, fall back to a local mirror if we have one.
            if self._mirror:
                directory = self._mirror.resolve() / job_dir
                return ServeResult(kind="file", directory=str(directory), filename=filename)
            return ServeResult(kind="missing")

    def read_html(self, job_dir: str, filename: str) -> Optional[str]:
        # Prefer the local mirror (fast, present after conversion); the bucket
        # is the durable copy.
        if self._mirror:
            p = _safe_join(self._mirror, job_dir, filename)
            if p and p.exists():
                return p.read_text(encoding="utf-8")
        return None  # (a bucket GET could be added here if no mirror exists)

    def write_html(self, job_dir: str, filename: str, html: str) -> None:
        # Write back to the HTML bucket (durable) and update the local mirror.
        oci_storage = self._oci()
        # Reuse the existing HTML upload helper (uploads to the HTML bucket).
        import tempfile
        import os as _os
        tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
        try:
            tmp.write(html)
            tmp.close()
            oci_storage.upload_html_to_bucket(tmp.name, self._html_object_name(job_dir, filename))
        finally:
            _os.unlink(tmp.name)
        if self._mirror:
            p = _safe_join(self._mirror, job_dir, filename)
            if p:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(html, encoding="utf-8")

    def put_upload(self, name: str, data) -> str:
        # Store the transient upload locally for the worker to read. (A pure-OCI
        # variant would upload to an `uploads/` bucket prefix and return its
        # object key; kept local here since the worker runs on the same host in
        # the thread-queue default and this avoids an unnecessary round-trip.)
        if self._uploads:
            self._uploads.mkdir(parents=True, exist_ok=True)
            dest = self._uploads / name
            if hasattr(data, "save"):
                data.save(str(dest))
            elif isinstance(data, (bytes, bytearray)):
                dest.write_bytes(data)
            else:
                dest.write_bytes(Path(data).read_bytes())
            return str(dest)
        raise RuntimeError("OciOutputStore.put_upload requires an upload_dir")

    def local_output_root(self) -> Optional[Path]:
        return self._mirror


def build_output_store(config_module) -> OutputStore:
    """Factory: pick the output backend from config.OUTPUT_BACKEND.

    Defaults to LocalOutputStore (current behavior). `oci` serves from the
    bucket with a local mirror fallback.
    """
    backend = getattr(config_module, "OUTPUT_BACKEND", "local")
    if backend == "local":
        return LocalOutputStore(config_module.OUTPUT_DIR, config_module.UPLOAD_DIR)
    if backend == "oci":
        return OciOutputStore(
            config_module,
            local_mirror=config_module.OUTPUT_DIR,
            upload_dir=config_module.UPLOAD_DIR,
        )
    raise ValueError(f"Unknown OUTPUT_BACKEND: {backend!r}")
