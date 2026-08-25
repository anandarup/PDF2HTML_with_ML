"""
OCI Object Storage integration.

Uploads media files to OCI bucket and returns public URLs.
Uses Instance Principal authentication (no config files needed on VM).
"""
import logging
import mimetypes
from pathlib import Path

import oci

_log = logging.getLogger(__name__)

BUCKET_NAME = "poc-interactivetxt-media-src-bucket"
REGION = "ap-hyderabad-1"

# Initialize OCI client with instance principal
_signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
_client = oci.object_storage.ObjectStorageClient({}, signer=_signer)
_namespace = _client.get_namespace().data


def get_public_url(object_name: str) -> str:
    """Get the public URL for an object in the bucket."""
    return (
        f"https://objectstorage.{REGION}.oraclecloud.com"
        f"/n/{_namespace}/b/{BUCKET_NAME}/o/{object_name}"
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
        _client.put_object(
            _namespace,
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

    Returns:
        Dict mapping relative local path -> public URL
    """
    url_map = {}

    if not local_dir.exists():
        return url_map

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
                _client.put_object(_namespace, BUCKET_NAME, obj_name, f, content_type=content_type)
            url_map[str(relative)] = get_public_url(obj_name)
        except Exception as exc:
            _log.warning(f"Failed to upload {relative}: {exc}")

    return url_map
