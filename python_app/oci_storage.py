"""
OCI Object Storage integration.

Uploads media/HTML to OCI buckets, reads objects back, and lists them by
prefix. Uses Instance Principal authentication (no config files needed on VM).

Client construction is lazy. It used to happen at import time, which meant
`import oci_storage` raised on any machine without the instance-metadata
endpoint -- laptops, CI, the test suite -- so every caller had to import the
module inside a try/except at the point of use. Now the signer is built on
first actual use, and only the call fails (with OciUnavailableError) rather
than the import.

Bucket names and region come from `config`, which reads them from the
environment. The defaults there are identical to the values this module
previously hardcoded, so behavior is unchanged unless an operator sets the
corresponding env var.
"""

from __future__ import annotations

import logging
import mimetypes
import threading
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

import oci

_log = logging.getLogger(__name__)

try:  # config is pure-stdlib, but never let it break this module's import
    import config as _config
except Exception:  # pragma: no cover
    _config = None


def _cfg(name: str, fallback: str) -> str:
    """Read a setting from config, falling back to the historical constant."""
    if _config is None:
        return fallback
    value = getattr(_config, name, fallback)
    return value if value not in (None, "") else fallback


REGION = _cfg("OCI_REGION", "ap-hyderabad-1")
BUCKET_NAME = _cfg("BUCKET_MEDIA", "poc-interactivetxt-media-src-bucket")
HTML_BUCKET_NAME = _cfg("BUCKET_HTML", "poc-interactivetxtbk1")
VIDEO_BUCKET_NAME = _cfg("BUCKET_VIDEO", "poc-interactivetxt-media-dst-bucket")

# OCI caps a single list_objects page at 1000 names.
_LIST_PAGE_SIZE = 1000
_LIST_FIELDS = "name,size,timeModified"

# RLock (not Lock) so _get_namespace can call _get_client while holding it.
_init_lock = threading.RLock()
_client = None
_namespace: Optional[str] = None


class OciUnavailableError(RuntimeError):
    """
    Raised when the OCI client cannot be constructed or the namespace cannot
    be resolved -- typically no instance-principal metadata endpoint (running
    off-VM) or missing IAM policy. Subclasses RuntimeError so existing callers
    that catch broad Exceptions keep working.
    """


def _get_client():
    """Build the Object Storage client on first use and cache it."""
    global _client
    if _client is None:
        with _init_lock:
            if _client is None:
                try:
                    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
                    _client = oci.object_storage.ObjectStorageClient({}, signer=signer)
                except Exception as exc:
                    raise OciUnavailableError(
                        "Could not build an OCI Object Storage client. This "
                        "requires instance-principal metadata, so it only works "
                        f"on an OCI instance with an IAM policy attached: {exc}"
                    ) from exc
    return _client


def _get_namespace() -> str:
    """
    Resolve the Object Storage namespace.

    Prefers the configured OCI_NAMESPACE, which avoids a network round-trip and
    lets get_public_url work with no credentials at all. Falls back to asking
    the SDK.
    """
    global _namespace
    if _namespace is None:
        with _init_lock:
            if _namespace is None:
                configured = _cfg("OCI_NAMESPACE", "")
                if configured:
                    _namespace = configured
                else:
                    try:
                        _namespace = _get_client().get_namespace().data
                    except OciUnavailableError:
                        raise
                    except Exception as exc:
                        raise OciUnavailableError(
                            f"Could not resolve the OCI namespace: {exc}"
                        ) from exc
    return _namespace


def is_available() -> bool:
    """True if a client and namespace can be obtained. Never raises."""
    try:
        _get_client()
        _get_namespace()
        return True
    except OciUnavailableError:
        return False


def get_public_url(object_name: str, bucket: str | None = None) -> str:
    """Get the public URL for an object in the bucket.

    Percent-encode the object name (it may contain spaces / em-dashes / other
    special characters from the job-dir/filename) so the resulting URL is
    valid and the browser can actually fetch the object.
    """
    return (
        f"https://objectstorage.{REGION}.oraclecloud.com"
        f"/n/{_get_namespace()}/b/{bucket or BUCKET_NAME}"
        f"/o/{quote(object_name, safe='/')}"
    )


def upload_file(local_path: Path, object_prefix: str = "") -> str:
    """
    Upload a file to OCI Object Storage.

    Args:
        local_path: Local file path to upload
        object_prefix: Prefix/folder in the bucket (e.g., 'images/job123/')

    Returns:
        Public URL of the uploaded object
    """
    if not local_path.exists():
        raise FileNotFoundError(f"File not found: {local_path}")

    object_name = f"{object_prefix}{local_path.name}" if object_prefix else local_path.name
    content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

    with open(local_path, "rb") as f:
        _get_client().put_object(
            _get_namespace(),
            BUCKET_NAME,
            object_name,
            f,
            content_type=content_type,
        )

    _log.info(f"Uploaded {local_path.name} to {BUCKET_NAME}/{object_name}")
    return get_public_url(object_name)


def upload_directory(local_dir: Path, object_prefix: str) -> dict:
    """
    Upload all files in a directory (recursively) to OCI.

    Note: `.md` and `.json` files are deliberately skipped -- they are
    conversion by-products, not learner-facing assets. Use `upload_bytes` for
    JSON sidecars that DO need to be published.

    Returns:
        Dict mapping relative local path -> public URL
    """
    url_map = {}

    if not local_dir.exists():
        return url_map

    client = _get_client()
    namespace = _get_namespace()

    for file_path in local_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.name.startswith(".") or file_path.suffix in (".md", ".json"):
            continue

        relative = file_path.relative_to(local_dir)
        obj_name = f"{object_prefix}{relative}"

        try:
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            with open(file_path, "rb") as f:
                client.put_object(namespace, BUCKET_NAME, obj_name, f, content_type=content_type)
            url_map[str(relative)] = get_public_url(obj_name)
        except Exception as exc:
            _log.warning(f"Failed to upload {relative}: {exc}")

    return url_map


def upload_html_to_bucket(local_path, object_name: str) -> str:
    """Upload the rendered HTML file to the HTML bucket."""
    local_path = Path(local_path)
    if not local_path.exists():
        return ""

    with open(local_path, "rb") as f:
        _get_client().put_object(
            _get_namespace(),
            HTML_BUCKET_NAME,
            object_name,
            f,
            content_type="text/html",
        )

    url = get_public_url(object_name, bucket=HTML_BUCKET_NAME)
    _log.info(f"Uploaded HTML to {HTML_BUCKET_NAME}/{object_name}")
    return url


def upload_bytes(
    data: bytes,
    object_name: str,
    bucket: str | None = None,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload an in-memory payload.

    The path for generated artifacts that never exist on disk, and for the
    `.json` sidecars that `upload_directory` filters out.

    Returns:
        Public URL of the uploaded object
    """
    target = bucket or BUCKET_NAME
    _get_client().put_object(
        _get_namespace(),
        target,
        object_name,
        data,
        content_type=content_type,
    )
    _log.info(f"Uploaded {len(data)} bytes to {target}/{object_name}")
    return get_public_url(object_name, bucket=target)


def get_object(object_name: str, bucket: str | None = None) -> Optional[bytes]:
    """
    Read an object's bytes out of a bucket.

    Returns:
        The object's content, or None if it does not exist.

    Raises:
        OciUnavailableError: if the client/namespace cannot be obtained.
        oci.exceptions.ServiceError: for any service error other than 404.
    """
    target = bucket or BUCKET_NAME
    try:
        response = _get_client().get_object(_get_namespace(), target, object_name)
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return None
        raise

    body = response.data
    # The SDK hands back a streaming body; .content materializes it.
    if hasattr(body, "content"):
        return body.content
    return body.raw.read()


def get_text(object_name: str, bucket: str | None = None,
             encoding: str = "utf-8") -> Optional[str]:
    """Read an object and decode it. Returns None if the object is missing."""
    raw = get_object(object_name, bucket=bucket)
    return None if raw is None else raw.decode(encoding)


def iter_objects(prefix: str = "", bucket: str | None = None) -> Iterator[dict]:
    """
    Yield every object under a prefix, transparently paginating.

    Yields:
        Dicts of {"name": str, "size": int | None, "time_modified": datetime | None}
    """
    client = _get_client()
    namespace = _get_namespace()
    target = bucket or BUCKET_NAME
    start = None

    while True:
        kwargs = {
            "prefix": prefix,
            "limit": _LIST_PAGE_SIZE,
            "fields": _LIST_FIELDS,
        }
        if start:
            kwargs["start"] = start

        response = client.list_objects(namespace, target, **kwargs)
        page = response.data

        for summary in page.objects or []:
            yield {
                "name": summary.name,
                "size": getattr(summary, "size", None),
                "time_modified": getattr(summary, "time_modified", None),
            }

        start = getattr(page, "next_start_with", None)
        if not start:
            return


def list_objects(prefix: str = "", bucket: str | None = None,
                 limit: int | None = None) -> list[dict]:
    """
    List objects under a prefix.

    Args:
        prefix: Object-name prefix to filter on (e.g. 'graph/v1/').
        bucket: Bucket to list. Defaults to the media bucket.
        limit: Stop after this many objects. None means all of them.

    Returns:
        List of {"name", "size", "time_modified"} dicts.
    """
    results: list[dict] = []
    for item in iter_objects(prefix=prefix, bucket=bucket):
        results.append(item)
        if limit is not None and len(results) >= limit:
            break
    return results


def object_exists(object_name: str, bucket: str | None = None) -> bool:
    """True if the object is present in the bucket."""
    target = bucket or BUCKET_NAME
    try:
        _get_client().head_object(_get_namespace(), target, object_name)
        return True
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return False
        raise


def delete_object(object_name: str, bucket: str | None = None) -> bool:
    """
    Delete a single object.

    Returns:
        True if the object was deleted, False if it was already absent (a
        404 here is not an error — the caller's goal, "this object is gone",
        is already satisfied).

    Raises:
        OciUnavailableError: if the client/namespace cannot be obtained.
        oci.exceptions.ServiceError: for any service error other than 404.
    """
    target = bucket or BUCKET_NAME
    try:
        _get_client().delete_object(_get_namespace(), target, object_name)
        _log.info(f"Deleted oci://{target}/{object_name}")
        return True
    except oci.exceptions.ServiceError as exc:
        if exc.status == 404:
            return False
        raise


def delete_prefix(prefix: str, bucket: str | None = None) -> dict:
    """
    Delete every object under a prefix.

    Object Storage has no bulk-delete-by-prefix call, so this lists (via
    iter_objects, transparently paginated) then deletes one at a time. A
    single object's failure does not abort the rest -- this is cleanup, and
    a partial delete that removes everything it can is strictly better than
    an all-or-nothing one that leaves everything behind over one bad object.

    Returns:
        {"deleted": int, "failed": list[str]} -- object names that raised a
        non-404 error are collected in "failed" rather than raising, so a
        caller cleaning up several buckets/prefixes can finish the others.
    """
    target = bucket or BUCKET_NAME
    deleted = 0
    failed: list[str] = []
    for obj in iter_objects(prefix=prefix, bucket=target):
        name = obj["name"]
        try:
            if delete_object(name, bucket=target):
                deleted += 1
        except Exception as exc:
            _log.warning(f"Failed to delete oci://{target}/{name}: {exc}")
            failed.append(name)
    return {"deleted": deleted, "failed": failed}
